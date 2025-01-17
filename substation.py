import segmentation_models as sm
import albumentations as A
import cv2
import tensorflow.keras as keras
import tensorflow as tf
from skimage.io import imread
import numpy as np
from matplotlib import pyplot as plt
import os
from glob import glob
import random
import itertools
from skimage import util
from tqdm import tqdm_notebook

import warnings
warnings.filterwarnings('ignore')




tf.config.run_functions_eagerly(True)
x_train_dir = '../input/electrical-substation-detection/train/image_chips'
y_train_dir = '../input/electrical-substation-detection/train/labels'

x_valid_dir = '../input/electrical-substation-detection/validation/image_chips'
y_valid_dir = '../input/electrical-substation-detection/validation/labels'


def visualize(**images):
    """PLot images in one row."""
    n = len(images)
    plt.figure(figsize=(16, 5))
    for i, (name, image) in enumerate(images.items()):
        plt.subplot(1, n, i + 1)
        plt.xticks([])
        plt.yticks([])
        plt.title(' '.join(name.split('_')).title())
        plt.imshow(image)
    plt.show()


# helper function for data visualization
def denormalize(x):
    """Scale image to range 0..1 for correct plot"""
    x_max = np.percentile(x, 98)
    x_min = np.percentile(x, 2)
    x = (x - x_min) / (x_max - x_min)
    x = x.clip(0, 1)
    return x


# classes for data loading and preprocessing
class Dataset:
    """CamVid Dataset. Read images, apply augmentation and preprocessing transformations.

    Args:
        images_dir (str): path to images folder
        masks_dir (str): path to segmentation masks folder
        class_values (list): values of classes to extract from segmentation mask
        augmentation (albumentations.Compose): data transfromation pipeline
            (e.g. flip, scale, etc.)
        preprocessing (albumentations.Compose): data preprocessing
            (e.g. noralization, shape manipulation, etc.)

    """

    CLASSES = ['nodetect', 'es']

    def __init__(
            self,
            images_dir,
            masks_dir,
            classes=None,
            augmentation=None,
            preprocessing=None,
    ):
        self.ids = os.listdir(images_dir)
        self.images_fps = [os.path.join(images_dir, image_id) for image_id in self.ids]
        self.masks_fps = [os.path.join(masks_dir, image_id) for image_id in self.ids]

        # convert str names to class values on masks
        self.class_values = [self.CLASSES.index(cls.lower()) for cls in classes]

        self.augmentation = augmentation
        self.preprocessing = preprocessing

    def __getitem__(self, i):

        # read data
        image = cv2.imread(self.images_fps[i])
        image = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
        mask = cv2.imread(self.masks_fps[i], 0)
        mask = mask / mask.max()

        # extract certain classes from mask (e.g. cars)
        masks = [(mask == v) for v in self.class_values]
        mask = np.stack(masks, axis=-1).astype('float')

        # add background if mask is not binary
        if mask.shape[-1] != 1:
            background = 1 - mask.sum(axis=-1, keepdims=True)
            mask = np.concatenate((mask, background), axis=-1)

        # apply augmentations
        if self.augmentation:
            sample = self.augmentation(image=image, mask=mask)
            image, mask = sample['image'], sample['mask']

        # apply preprocessing
        if self.preprocessing:
            sample = self.preprocessing(image=image, mask=mask)
            image, mask = sample['image'], sample['mask']

        return image, mask

    def __len__(self):
        return len(self.ids)


class Dataloder(keras.utils.Sequence):
    """Load data from dataset and form batches

    Args:
        dataset: instance of Dataset class for image loading and preprocessing.
        batch_size: Integet number of images in batch.
        shuffle: Boolean, if `True` shuffle image indexes each epoch.
    """

    def __init__(self, dataset, batch_size=1, shuffle=False):
        self.dataset = dataset
        self.batch_size = batch_size
        self.shuffle = shuffle
        self.indexes = np.arange(len(dataset))

        self.on_epoch_end()

    def __getitem__(self, i):

        # collect batch data
        start = i * self.batch_size
        stop = (i + 1) * self.batch_size
        data = []
        for j in range(start, stop):
            data.append(self.dataset[j])

        # transpose list of lists
        batch = [np.stack(samples, axis=0) for samples in zip(*data)]

        return batch[0], batch[1]

    def __len__(self):
        """Denotes the number of batches per epoch"""
        return len(self.indexes) // self.batch_size

    def on_epoch_end(self):
        """Callback function to shuffle indexes each epoch"""
        if self.shuffle:
            self.indexes = np.random.permutation(self.indexes)

# Lets look at data we have
dataset = Dataset(x_train_dir, y_train_dir, classes=['NoDetect', 'ES'])

image, mask = dataset[10] # get some sample
visualize(
    image=image,
    substation_mask=mask[..., 1].squeeze(),
)

IMG_SIZE = 512


def round_clip_0_1(x, **kwargs):
    return x.round().clip(0, 1)


def normalize_albumenation(x, **kwargs):
    return x


# define heavy augmentations
def get_training_augmentation():
    train_transform = [

        A.HorizontalFlip(p=0.5),

        A.ShiftScaleRotate(scale_limit=0.1, rotate_limit=30, shift_limit=0.1, p=1, border_mode=0),

        A.PadIfNeeded(min_height=IMG_SIZE, min_width=IMG_SIZE, always_apply=True, border_mode=0),
        A.RandomCrop(height=IMG_SIZE, width=IMG_SIZE, always_apply=True),

        #         A.IAAAdditiveGaussianNoise(p=0.2),
        # A.IAAPerspective(p=0.5),

        #         A.OneOf(
        #             [
        #                 A.CLAHE(p=1),
        #                 A.RandomBrightness(p=1),
        #                 A.RandomGamma(p=1),
        #             ],
        #             p=0.9,
        #         ),

        # A.OneOf(
        #     [
        #         A.IAASharpen(p=1),
        #         A.Blur(blur_limit=3, p=1),
        #         A.MotionBlur(blur_limit=3, p=1),
        #     ],
        #     p=0.9,
        # ),

        #         A.OneOf(
        #             [
        #                 A.RandomContrast(p=1),
        #                 A.HueSaturationValue(p=1),
        #             ],
        #             p=0.9,
        #         ),
        A.Lambda(mask=round_clip_0_1)
    ]
    return A.Compose(train_transform)


def get_validation_augmentation():
    """Add paddings to make image shape divisible by 32"""
    test_transform = [
        #  A.PadIfNeeded(min_height=320, min_width=320, always_apply=True, border_mode=0),
        A.RandomCrop(height=IMG_SIZE, width=IMG_SIZE, always_apply=True)
        # A.PadIfNeeded(384, 480)
    ]
    return A.Compose(test_transform)


def get_preprocessing(preprocessing_fn):
    """Construct preprocessing transform

    Args:
        preprocessing_fn (callbale): data normalization function
            (can be specific for each pretrained neural network)
    Return:
        transform: albumentations.Compose

    """

    _transform = [
        A.Lambda(image=preprocessing_fn),
        A.Lambda(image=normalize_albumenation)
    ]
    return A.Compose(_transform)



#  Lets look at augmented data we have
dataset = Dataset(x_train_dir, y_train_dir, classes=['nodetect', 'es'], augmentation=get_training_augmentation())

image, mask = dataset[10] # get some sample
visualize(
    image=image,
    substation_mask=mask[..., 1].squeeze(),
)

BACKBONE = 'resnet34'
BATCH_SIZE = 8
CLASSES = ['es']
LR = 0.0001
EPOCHS = 150
version = 8

preprocess_input = sm.get_preprocessing(BACKBONE)
# define network parameters
n_classes = 1 if len(CLASSES) == 1 else (len(CLASSES) + 1)  # case for binary and multiclass segmentation
activation = 'sigmoid' if n_classes == 1 else 'softmax'

tf.keras.backend.clear_session()
#create mode
# model = sm.PSPNet(BACKBONE, classes=n_classes, activation=activation)
# model = sm.FPN(BACKBONE, classes=n_classes, activation=activation)
model = sm.Unet(BACKBONE, classes=n_classes, activation=activation, encoder_weights='imagenet') #'imagenet')#, encoder_freeze=True)
# model = sm.Unet( classes=n_classes, activation=activation)
# define optomizer
optim = keras.optimizers.Adam(LR)

# Segmentation models losses can be combined together by '+' and scaled by integer or float factor
dice_loss = sm.losses.DiceLoss()
focal_loss = sm.losses.BinaryFocalLoss() if n_classes == 1 else sm.losses.CategoricalFocalLoss()
jacard_loss = sm.losses.JaccardLoss()

# total_loss =dice_loss + (1*focal_loss) + (1*jacard_loss) + (1*bce_loss)
total_loss = dice_loss+jacard_loss+focal_loss


# actulally total_loss can be imported directly from library, above example just show you how to manipulate with losses
# total_loss = sm.losses.binary_focal_dice_loss # or sm.losses.categorical_focal_dice_loss

metrics = [sm.metrics.IOUScore(threshold=0.5), sm.metrics.FScore(threshold=0.5)]

# compile keras model with defined optimozer, loss and metrics
model.compile(optim, total_loss, metrics)

# Dataset for train images
train_dataset = Dataset(
    x_train_dir,
    y_train_dir,
    classes=CLASSES,
    augmentation=get_training_augmentation(),
    preprocessing=get_preprocessing(preprocess_input),
)

# Dataset for validation images
valid_dataset = Dataset(
    x_valid_dir,
    y_valid_dir,
    classes=CLASSES,
    augmentation=get_validation_augmentation(),
    preprocessing=get_preprocessing(preprocess_input),
)

train_dataloader = Dataloder(train_dataset, batch_size=BATCH_SIZE, shuffle=True)
valid_dataloader = Dataloder(valid_dataset, batch_size=1, shuffle=False)

# check shapes for errors
assert train_dataloader[0][0].shape == (BATCH_SIZE, IMG_SIZE, IMG_SIZE, 3)
assert train_dataloader[0][1].shape == (BATCH_SIZE, IMG_SIZE, IMG_SIZE, n_classes)

# define callbacks for learning rate scheduling and best checkpoints saving
callbacks = [
    keras.callbacks.ModelCheckpoint('/kaggle/working/best_model_v%d.h5'%version, save_weights_only=True,save_best_only=True, mode='min', verbose=1),
    keras.callbacks.ReduceLROnPlateau(factor=0.5, verbose=1),
]

with open('/kaggle/working/ModelSummary_v%d.txt'%version, 'w') as f:
  model.summary(print_fn=f.write)
# model.summary()

# train model
history = model.fit_generator(
    train_dataloader,
    steps_per_epoch=len(train_dataloader),
    epochs=EPOCHS,
    callbacks=callbacks,
    validation_data=valid_dataloader,
    validation_steps=len(valid_dataloader),
)

# Plot training & validation iou_score values
plt.figure(figsize=(30, 10))
plt.subplot(121)
plt.plot(history.history['iou_score'])
plt.plot(history.history['val_iou_score'])
plt.title('Model iou_score')
plt.ylabel('iou_score')
plt.xlabel('Epoch')
plt.legend(['Train', 'Test'], loc='upper left')

# Plot training & validation loss values
plt.subplot(122)
plt.plot(history.history['loss'])
plt.plot(history.history['val_loss'])
plt.title('Model loss')
plt.ylabel('Loss')
plt.xlabel('Epoch')
plt.legend(['Train', 'Test'], loc='upper left')
plt.savefig('/kaggle/working/TrainingSummary_v%d.jpg'%version)
plt.show()
test_img = imread('../input/electrical-substation-detection/test/mosaic_test.jpg')
out_mask = np.zeros((test_img.shape[0], test_img.shape[1]))

img_x, img_y = 768, 768

# test_model = keras.models.load_model('/kaggle/working/best_model_v%d.h5'%version)
model.load_weights('/kaggle/working/best_model_v%d.h5'%version)
for i in range(5):
    for j in range(5):
        image = np.zeros((768,768,3))
        image[:750, :750] = test_img[i*750:(i+1)*750, j*750:(j+1)*750]
        image = get_preprocessing(preprocessing_fn=preprocess_input)(image=image)['image']
        image = np.expand_dims(image, axis=0)
        out_mask[i*750:(i+1)*750, j*750:(j+1)*750] = model.predict(image)[0,:750,:750,0]


visualize(
    image=denormalize(test_img.squeeze()),
    out_mask=out_mask,
    out_mask_rounded=out_mask.round()
)

plt.imsave('/kaggle/working/OutMaskv%d.jpg'%version, out_mask.round(), cmap='gray')
np.save("/kaggle/working/out_mask_v%d.npy"%version, np.array(out_mask))

i=1
j=3

image = np.zeros((768,768,3))

image[:750, :750] = test_img[i*750:(i+1)*750, j*750:(j+1)*750]
image = get_preprocessing(preprocessing_fn=preprocess_input)(image=image)['image']
image = np.expand_dims(image, axis=0)
out_mask= model.predict(image)[0,:750,:750,0]

visualize(Input=test_img[i*750:(i+1)*750, j*750:(j+1)*750], Model_Results=out_mask, Predicted=out_mask.round())