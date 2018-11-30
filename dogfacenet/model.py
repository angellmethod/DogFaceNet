"""
DogFaceNet
The main DogFaceNet implementation

Licensed under the MIT License (see LICENSE for details)
Written by Guillaume Mougeot
"""

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import matplotlib.pyplot as plt

from tqdm import tqdm, trange

import tensorflow as tf

from losses import arcface_loss
from dataset import get_dataset

# Paths of images folders
PATH_BG = "../data/bg/"
PATH_DOG1 = "../data/dog1/"

# Images parameters for network feeding
IM_H = 224
IM_W = 224
IM_C = 3

# Training parameters:
EPOCHS = 100
BATCH_SIZE = 32
TRAIN_SPLIT = 0.8

# Embedding size
EMB_SIZE = 128


############################################################
#  Data pre-processing
############################################################


# Retrieve dataset from folders
# filenames_train, labels_train, filenames_valid, labels_valid = get_dataset(
#     PATH_BG, PATH_DOG1, TRAIN_SPLIT)
filenames_train, labels_train, filenames_valid, labels_valid, count_labels = get_dataset()

# Filenames and labels place holder
filenames_train_placeholder = tf.placeholder(
    filenames_train.dtype, filenames_train.shape)
labels_train_placeholder = tf.placeholder(tf.int64, labels_train.shape)

filenames_valid_placeholder = tf.placeholder(
    filenames_valid.dtype, filenames_valid.shape)
labels_valid_placeholder = tf.placeholder(tf.int64, labels_valid.shape)

# Defining dataset

# Opens an image file, stores it into a tf.Tensor and reshapes it
def _parse_function(filename, label):
    image_string = tf.read_file(filename)
    image_decoded = tf.image.decode_jpeg(image_string, channels=3)
    image_resized = tf.image.resize_images(image_decoded, [IM_H, IM_W])
    return image_resized, label


data_train = tf.data.Dataset.from_tensor_slices(
    (filenames_train_placeholder, labels_train_placeholder))
data_train = data_train.map(_parse_function)

data_valid = tf.data.Dataset.from_tensor_slices((filenames_valid_placeholder,labels_valid_placeholder))
data_valid = data_valid.map(_parse_function)

# Batch the dataset for training
data_train = data_train.shuffle(1000).batch(BATCH_SIZE)
iterator = data_train.make_initializable_iterator()
next_element = iterator.get_next()

data_valid = data_valid.batch(BATCH_SIZE)
it_valid = data_valid.make_initializable_iterator()
next_valid = it_valid.get_next()

# Define the global step and dropout rate
# global_step = tf.Variable(name='global_step', initial_value=0, trainable=False)
# inc_op = tf.assign_add(global_step, 1, name='increment_global_step')
# dropout_rate = tf.placeholder(name='dropout_rate', dtype=tf.float32)


############################################################
#  Models
############################################################


class Dummy_embedding(tf.keras.Model):
    def __init__(self, emb_size):
        super(Dummy_embedding, self).__init__(name='dummy')
        self.conv1 = tf.keras.layers.Conv2D(10,(3, 3))
        self.pool1 = tf.keras.layers.MaxPooling2D((2, 2))
        self.conv2 = tf.keras.layers.Conv2D(20,(3, 3))
        self.pool2 = tf.keras.layers.MaxPooling2D((2, 2))
        self.conv3 = tf.keras.layers.Conv2D(40,(3, 3))
        self.pool3 = tf.keras.layers.MaxPooling2D((2, 2))
        self.conv4 = tf.keras.layers.Conv2D(80,(3, 3))
        self.avg_pool = tf.keras.layers.GlobalAveragePooling2D()
        self.dense = tf.layers.Dense(emb_size)
    
    def __call__(self, input_tensor):
        x = self.conv1(input_tensor)
        x = self.pool1(x)
        x = self.conv2(x)
        x = self.pool2(x)
        x = self.conv3(x)
        x = self.pool3(x)
        x = self.avg_pool(x)
        x = self.dense(x)

        return tf.nn.l2_normalize(x)

class ResnetIdentityBlock(tf.keras.Model):
    def __init__(self, kernel_size, filters):
        super(ResnetIdentityBlock, self).__init__(name='')
        filters1, filters2, filters3 = filters

        self.conv2a = tf.keras.layers.Conv2D(filters1, (1, 1))
        self.bn2a = tf.keras.layers.BatchNormalization()

        self.conv2b = tf.keras.layers.Conv2D(filters2, kernel_size, padding='same')
        self.bn2b = tf.keras.layers.BatchNormalization()

        self.conv2c = tf.keras.layers.Conv2D(filters3, (1, 1))
        self.bn2c = tf.keras.layers.BatchNormalization()

    def call(self, input_tensor, training=False):
        x = self.conv2a(input_tensor)
        x = self.bn2a(x, training=training)
        x = tf.nn.relu(x)

        x = self.conv2b(x)
        x = self.bn2b(x, training=training)
        x = tf.nn.relu(x)

        x = self.conv2c(x)
        x = self.bn2c(x, training=training)

        x += input_tensor
        return tf.nn.relu(x)

class ResnetConvBlock(tf.keras.Model):
    def __init__(self, kernel_size, filters):
        super(ResnetConvBlock, self).__init__(name='')
        filters1, filters2, filters3 = filters

        self.conv2a = tf.keras.layers.Conv2D(filters1, (1, 1))
        self.bn2a = tf.keras.layers.BatchNormalization()

        self.conv2b = tf.keras.layers.Conv2D(filters2, kernel_size, padding='same')
        self.bn2b = tf.keras.layers.BatchNormalization()

        self.conv2c = tf.keras.layers.Conv2D(filters3, (1, 1))
        self.bn2c = tf.keras.layers.BatchNormalization()

        self.conv1 = tf.keras.layers.Conv2D(filters3, (1, 1))
        self.bn1 = tf.keras.layers.BatchNormalization()

    def call(self, input_tensor, training=False):
        x = self.conv2a(input_tensor)
        x = self.bn2a(x, training=training)
        x = tf.nn.relu(x)

        x = self.conv2b(x)
        x = self.bn2b(x, training=training)
        x = tf.nn.relu(x)

        x = self.conv2c(x)
        x = self.bn2c(x, training=training)

        shortcut = self.conv1(input_tensor)
        shortcut = self.bn1(shortcut, training=training)

        x += shortcut
        return tf.nn.relu(x)

class ResNet_embedding(tf.keras.Model):
    def __init__(self, emb_size):
        super(ResNet_embedding, self).__init__(name='resnet')
        self.conv1_pad = tf.keras.layers.ZeroPadding2D(padding=(3,3))
        self.conv1 = tf.keras.layers.Conv2D(64, (7, 7), strides=(2, 2))
        self.bn_conv1 = tf.keras.layers.BatchNormalization()

        self.pool1_pad = tf.keras.layers.ZeroPadding2D(padding=(1,1))
        self.pool1 = tf.keras.layers.MaxPooling2D((3, 3), strides=(2, 2))

        #filters = [[64,64,256], [128,128,512], [256,256,1024], [512,512,2048]]
        #nrof_identity_block = [2,3,5,2]
        filters = [[64,64,256]]
        nrof_identity_block = [1]

        self.in_layers = []
        for i in range(len(filters)):
            self.in_layers += [ResnetConvBlock(3, filters[i])]
            for _ in range(nrof_identity_block[i]):
                self.in_layers += [ResnetIdentityBlock(3, filters[i])]
        
        self.avg_pool = tf.keras.layers.GlobalAveragePooling2D()
        self.embedding = tf.keras.layers.Dense(emb_size)

    def __call__(self, input_tensor=None, training=False):
        x = self.conv1_pad(input_tensor)
        x = self.conv1(x)
        x = self.bn_conv1(x, training=training)
        x = tf.nn.relu(x)
        x = self.pool1_pad(x)
        x = self.pool1(x)

        for in_layer in self.in_layers:
            x = in_layer(x, training=training)
        
        x = self.avg_pool(x)
        x = self.embedding(x)

        return tf.nn.l2_normalize(x)


class NASNet_embedding(tf.keras.Model):
    def __init__(self):
        super(NASNet_embedding, self).__init__(name='')

        self.pool = tf.keras.layers.GlobalAveragePooling2D()
        self.dense_1 = tf.layers.Dense(1056, activation='relu')
        self.dropout = tf.layers.Dropout(0.5)
        self.dense_2 = tf.layers.Dense(EMB_SIZE)

    def __call__(self, input_tensor, input_shape=(224, 224, 3), training=True, unfreeze=True):
        # base_model = tf.keras.applications.NASNetMobile(
        #         input_tensor=input_tensor,
        #         input_shape=input_shape,
        #         include_top=False
        #         )

        # for layer in base_model.layers: layer.trainable = False
        # x = self.pool(base_model.output)
        x = self.pool(input_tensor)
        x = self.dense_1(x)
        if training:
            x = self.dropout(x)
        x = self.dense_2(x)

        return tf.keras.backend.l2_normalize(x)


model = Dummy_embedding(EMB_SIZE)

# Training
next_images, next_labels = next_element

output = model(next_images)

logit = arcface_loss(embedding=output, labels=next_labels,
                     w_init=None, out_num=count_labels)
loss = tf.reduce_mean(tf.nn.sparse_softmax_cross_entropy_with_logits(
    logits=logit, labels=next_labels))

# Validation
next_images_valid, next_labels_valid = next_valid

output_valid = model(next_images_valid)

logit_valid = arcface_loss(embedding=output_valid, labels=next_labels_valid,
                     w_init=None, out_num=count_labels)
loss_valid = tf.reduce_mean(tf.nn.sparse_softmax_cross_entropy_with_logits(
    logits=logit_valid, labels=next_labels_valid))

pred_valid = tf.nn.softmax(logit_valid)
acc_valid = tf.reduce_mean(tf.cast(tf.equal(tf.argmin(pred_valid, axis=1), next_labels_valid), dtype=tf.float32))

# Optimizer
lr = 0.01

opt = tf.train.AdamOptimizer(learning_rate=lr)
train = opt.minimize(loss)

# Accuracy for validation and testing
pred = tf.nn.softmax(logit)
acc = tf.reduce_mean(tf.cast(tf.equal(tf.argmin(pred, axis=1), next_labels), dtype=tf.float32))


############################################################
#  Training session
############################################################


init = tf.global_variables_initializer()

with tf.Session() as sess:

    summary = tf.summary.FileWriter('../output/summary', sess.graph)
    summaries = []
    for var in tf.trainable_variables():
        summaries.append(tf.summary.histogram(var.op.name, var))
    summaries.append(tf.summary.scalar('inference_loss', loss))
    summary_op = tf.summary.merge(summaries)
    saver = tf.train.Saver(max_to_keep=100)

    sess.run(init)

    # Training
    nrof_batches = len(filenames_train)//BATCH_SIZE + 1
    nrof_batches_valid = len(filenames_train)//BATCH_SIZE + 1

    print("Start of training...")
    for i in range(EPOCHS):
        
        feed_dict = {filenames_train_placeholder: filenames_train,
                     labels_train_placeholder: labels_train}

        sess.run(iterator.initializer, feed_dict=feed_dict)

        feed_dict_valid = {filenames_valid_placeholder: filenames_valid,
                           labels_valid_placeholder: labels_valid}

        sess.run(it_valid.initializer, feed_dict=feed_dict_valid)

        # Training
        for j in trange(nrof_batches):
            try:
                _, loss_value, summary_op_value, acc_value = sess.run((train, loss, summary_op, acc))
                # summary.add_summary(summary_op_value, count)
                tqdm.write("\n Batch: " + str(j)
                    + ", Loss: " + str(loss_value)
                    + ", Accuracy: " + str(acc_value)
                    )

            except tf.errors.OutOfRangeError:
                break
        
        # Validation
        print("Start validation...")
        tot_acc = 0
        for _ in trange(nrof_batches_valid):
            try:
                loss_valid_value, acc_valid_value = sess.run((loss_valid, acc_valid))
                tot_acc += acc_valid_value
                tqdm.write("Loss: " + str(loss_valid_value)
                    + ", Accuracy: " + str(acc_valid_value)
                    )

            except tf.errors.OutOfRangeError:
                break
        print("End of validation. Total accuray: " + str(tot_acc/nrof_batches_valid))


    print("End of training.")
    print("Start evaluation...")
    # Evaluation on the validation set:
    ## One-shot training
    #sess.run()


