# -*- coding: utf-8 -*-
"""gcn.ipynb

Automatically generated by Colaboratory.

Original file is located at
    https://colab.research.google.com/drive/1JaeMuTa0vyJ5p_Xv7_-_JEQmzhBvBEVe
"""

from __future__ import print_function, division
import scipy

from keras.datasets import mnist
from keras.layers import Input, Dense, Reshape, Flatten, Dropout, Concatenate
from keras.layers import BatchNormalization, Activation, ZeroPadding2D, Add
from keras.layers.advanced_activations import PReLU, LeakyReLU
from keras.layers.convolutional import UpSampling2D, Conv2D
from keras.applications import VGG19
from keras.models import Sequential, Model
from keras.optimizers import Adam
import datetime
import matplotlib.pyplot as plt
import sys
import numpy as np
import os
import skimage
import cv2

import keras.backend as K

import scipy
from glob import glob
import numpy as np
import matplotlib.pyplot as plt

if not os.path.exists('datasets/'):
    os.makedirs('datasets/')

!pip install opencv-python

"""https://www.sentinel-hub.com/"""

class DataLoader():
  def __init__(self, dataset_name, img_res=(256, 256)):
    self.dataset_name = dataset_name
    self.img_res = img_res

  def load_data(self, batch_size=1, is_testing=False):
    data_type = "train" if not is_testing else "test"
    path = sorted(glob('./datasets/%s/*' % (self.dataset_name)))
    batch_images = np.random.choice(path, size=batch_size)
    imgs_hr = []
    imgs_lr = []
    for img_path in batch_images:
      img = self.imread(img_path)
      h, w = self.img_res
      low_h, low_w = int(h / 4), int(w / 4)
      img_hr = cv2.resize(img, self.img_res)
      img_lr = cv2.resize(img, (low_h, low_w))

      # If training => do random flip
      if not is_testing and np.random.random() < 0.5:
        img_hr = np.fliplr(img_hr)
        img_lr = np.fliplr(img_lr)
      
      imgs_hr.append(img_hr)
      imgs_lr.append(img_lr)
    imgs_hr = np.array(imgs_hr) / 127.5 - 1.
    imgs_lr = np.array(imgs_lr) / 127.5 - 1.
    return imgs_hr, imgs_lr

  def imread(self, path):
    return plt.imread(path).astype(np.float)

class SRGAN():
  def __init__(self):
    # Input shape
    self.channels = 3
    self.lr_height = 64                 # Low resolution height
    self.lr_width = 64                  # Low resolution width
    self.lr_shape = (self.lr_height, self.lr_width, self.channels)
    self.hr_height = self.lr_height*4   # High resolution height
    self.hr_width = self.lr_width*4     # High resolution width
    self.hr_shape = (self.hr_height, self.hr_width, self.channels)

    # Number of residual blocks in the generator
    self.n_residual_blocks = 10

    optimizer = Adam(0.0002, 0.)

    # Configure data loader
    self.dataset_name = 'texture' #'sat_imgs' #'satellite_images' #'img_align_celeba'
    self.data_loader = DataLoader(dataset_name=self.dataset_name,
                                  img_res=(self.hr_height, self.hr_width))
    self.data_loader_test = DataLoader(dataset_name='texture_test',
                                  img_res=(self.hr_height, self.hr_width))

    # Calculate output shape of D (PatchGAN)
    patch = int(self.hr_height / 2**4)
    self.disc_patch = (patch, patch, 1)

    # Number of filters in the first layer of G and D
    self.gf = 64
    self.df = 64

    # Build and compile the feature extractor	
    self.feature_extractor = self.build_feature_extractor()
    self.feature_extractor.compile(loss=['binary_crossentropy','mse'], optimizer=optimizer)

    # Build and compile the generator
    self.generator = self.build_generator()
    self.generator.compile(loss='mse', optimizer=optimizer)

    # High res. and low res. images
    img_hr = Input(shape=self.hr_shape)
    img_lr = Input(shape=self.lr_shape)

    # Generate high res. version from low res.
    fake_hr = self.generator(img_lr)

    # set features creator
    layer_name = 'block_1'
    self.features_model = Model(inputs=self.feature_extractor.input, outputs=self.feature_extractor.get_layer(layer_name).output)
    self.features_model.trainable = False
    self.features_model.compile(loss='mse', optimizer=optimizer, metrics=['accuracy'])

    # Extract image features of the generated img
    fake_features = self.features_model(fake_hr)
    # Adversarial loss
    validity, _ = self.feature_extractor(fake_hr)
    # For the combined model we will only train the generator
    self.feature_extractor.trainable = False
    self.combined = Model(img_lr, [validity, fake_features])
    self.combined.compile(loss=['binary_crossentropy','mse'], loss_weights=[1e-3, 1],  optimizer=optimizer)


  def build_generator(self):
    def residual_block(layer_input):
      """Residual block described in paper"""
      d = Conv2D(64, kernel_size=3, strides=1, padding='same')(layer_input)
      d = BatchNormalization(momentum=0.8)(d)
      d = PReLU()(d)
      d = Conv2D(64, kernel_size=3, strides=1, padding='same')(d)
      d = BatchNormalization(momentum=0.8)(d)
      d = Add()([d, layer_input])
      return d

    def deconv2d(layer_input):
      """Layers used during upsampling"""
      u = Conv2D(256, kernel_size=3, strides=1, padding='same')(layer_input)
      u = UpSampling2D(size=2)(u)
      u = PReLU()(u)
      return u

    # Low resolution image input
    img_lr = Input(shape=self.lr_shape)

    # Pre-residual block
    c1 = Conv2D(64, kernel_size=9, strides=1, padding='same')(img_lr)
    c1 = PReLU()(c1)

    # Propogate through residual blocks
    r = residual_block(c1)
    for _ in range(self.n_residual_blocks - 1):
      r = residual_block(r)

    # Post-residual block
    c2 = Conv2D(64, kernel_size=3, strides=1, padding='same')(r)
    c2 = BatchNormalization(momentum=0.8)(c2)
    c2 = Add()([c2, c1])

    # Upsampling
    u1 = deconv2d(c2)
    u2 = deconv2d(u1)

    # Generate high resolution output
    gen_hr = Conv2D(self.channels, kernel_size=9, strides=1, padding='same', activation='tanh')(u2)
    return Model(img_lr, gen_hr)

  def build_feature_extractor(self):
    def d_block(layer_input, filters, strides=1, bn=True, block_name='block_N'):
      """Discriminator layer"""
      d = Conv2D(filters, kernel_size=3, strides=strides, padding='same')(layer_input)
      if bn:
        d = BatchNormalization(momentum=0.8)(d)
      d = LeakyReLU(alpha=0.2, name=block_name)(d)
      return d
    def u_block(layer_input, filters, strides=1, bn=True):
      if bn:
        d = UpSampling2D((2,2))(layer_input)
        d = Conv2D(filters, (3,3), strides=strides, padding='same')(d)
        d = BatchNormalization(momentum=0.9)(d)
        d = LeakyReLU(alpha=0.2)(d)
      else:
        d = Conv2D(filters, (3,3), strides=strides, padding='same', activation='sigmoid')(layer_input)
        d = LeakyReLU(alpha=0.2)(d)
      return d


    # Input img
    d0 = Input(shape=self.hr_shape)

    d1 = d_block(d0, self.df, bn=False, block_name='block_1')
    d2 = d_block(d1, self.df, strides=2, bn=False, block_name='block_2')
    d3 = d_block(d2, self.df*2, block_name='block_3')
    d4 = d_block(d3, self.df*2, strides=2, block_name='block_4')
    d5 = d_block(d4, self.df*4, block_name='block_5')
    d6 = d_block(d5, self.df*4, strides=2, block_name='block_6')
    d7 = d_block(d6, self.df*8, block_name='block_7')
    d8 = d_block(d7, self.df*8, strides=2, block_name='block_8')
    d9 = u_block(d8, self.df*8)
    d9 = u_block(d9, self.df*8, bn=False)
    d10 = u_block(d9, self.df*4)
    d10 = u_block(d10, self.df*4, bn=False)
    d11 = u_block(d10, self.df*2)
    d11 = u_block(d11, self.df*2, bn=False)
    d12 = u_block(d11, self.df)
    d13 = u_block(d12, self.channels, bn=False)

    d9_bis = Dense(self.df*16)(d8)
    d10_bis = LeakyReLU(alpha=0.2)(d9_bis)
    validity = Dense(1, activation='sigmoid')(d10_bis)
    return Model(d0, [validity, d13])

  def train(self, epochs, batch_size=1, save_interval=50):
    start_time = datetime.datetime.now()
    i = 0
    for epoch in range(epochs):
      # ----------------------
      #  Train Feature Extractor
      # ----------------------

      # Sample images and their conditioning counterparts
      imgs_hr, imgs_lr = self.data_loader.load_data(batch_size)
      # imgs_hr, imgs_lr = np.random.randn(batch_size, 256, 256, 3), np.random.randn(batch_size, 64, 64, 3)

      # From low res. image generate high res. version
      fake_hr = self.generator.predict(imgs_lr)

      valid = np.ones((batch_size,) + self.disc_patch)
      fake = np.zeros((batch_size,) + self.disc_patch)

      # Train the discriminators (original images = real / generated = Fake)
      d_loss_real = self.feature_extractor.train_on_batch(imgs_hr, [valid, imgs_hr])
      d_loss_fake = self.feature_extractor.train_on_batch(fake_hr, [fake, fake_hr])
      d_loss = 0.5 * np.add(d_loss_real, d_loss_fake)

      # ------------------
      #  Train Generator
      # ------------------

      # Sample images and their conditioning counterparts
      imgs_hr, imgs_lr = self.data_loader.load_data(batch_size)
      # imgs_hr, imgs_lr = np.random.randn(batch_size, 256, 256, 3), np.random.randn(batch_size, 64, 64, 3)

      # The generators want the discriminators to label the generated images as real
      valid = np.ones((batch_size,) + self.disc_patch)

      # Extract ground truth image features using pre-trained VGG19 model
      image_features = self.features_model.predict(imgs_hr)
	    
      # Train the generators
      g_loss = self.combined.train_on_batch(imgs_lr, [valid, image_features])

      elapsed_time = datetime.datetime.now() - start_time
      # Plot the progress
      print ("%d time: %s" % (epoch, elapsed_time))
      i=i+1
      log_mesg = "%d: [D loss: %f]" % (i, d_loss[0])
      log_mesg = "%s  [G loss: %f]" % (log_mesg, g_loss[0])
      print(log_mesg)

      # If at save interval => save generated image samples
      if epoch % save_interval == 0:
        self.save_imgs(epoch)
        filename = "model_%d.h5" % epoch
        self.generator.save_weights(filename)
        filename_disc = "extractor_%d.h5" % epoch
        self.feature_extractor.save_weights(filename_disc)

  def save_imgs(self, epoch):
    if not os.path.exists('images/'):
      os.makedirs('images/')
    r, c = 2, 3
    imgs_hr, imgs_lr = self.data_loader_test.load_data(batch_size=2, is_testing=True)
    # imgs_hr, imgs_lr = np.random.randn(2, 256, 256, 3), np.random.randn(2, 64, 64, 3)
    fake_hr = self.generator.predict(imgs_lr)

    # Rescale images 0 - 1
    imgs_lr = 0.5 * imgs_lr + 0.5
    fake_hr = 0.5 * fake_hr + 0.5
    imgs_hr = 0.5 * imgs_hr + 0.5

    # Save generated images and the high resolution originals
    titles = ['LR', 'HR Generated', 'HR Original']
    plt.figure(figsize=(25,20))
    fig, axs = plt.subplots(r, c)
    cnt = 0
    for row in range(r):
      for col, image in enumerate([imgs_lr, fake_hr, imgs_hr]):
        axs[row, col].imshow(image[row])
        if row==0:
          axs[row, col].set_title(titles[col])
          axs[row, col].axis('off')
          cnt += 1
    fig.savefig("images/images_%d.png" % epoch)
    plt.close()

# For training
gcn = SRGAN()
gcn.train(epochs=2, batch_size=2, save_interval=1)
gcn.save_imgs(1)

# For inference with pre-trained weights
gcn = SRGAN()
gcn.generator.load_weights('model_1.h5')
gcn.save_imgs(2)