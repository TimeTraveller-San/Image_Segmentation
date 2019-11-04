import os
import cv2
import collections
import time
import tqdm

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import seaborn as sns

from sklearn.model_selection import KFold, train_test_split

import torchvision
import torchvision.transforms as transforms
import torch
from torch.utils.data import TensorDataset, DataLoader,Dataset
import torch.nn as nn
import torch.nn.functional as F
import torch.optim as optim
from torch.optim import lr_scheduler
from torch.optim.lr_scheduler import StepLR, ReduceLROnPlateau, CosineAnnealingLR

import albumentations as albu
import configparser
import argparse
import wandb

# Catalyst is amazing.
from catalyst.data import Augmentor
from catalyst.dl import utils
from catalyst.data.reader import ImageReader, ScalarReader, ReaderCompose, LambdaReader
# from catalyst.dl.runner import SupervisedRunner
from catalyst.dl.runner import SupervisedWandbRunner as SupervisedRunner
from catalyst.contrib.models.segmentation import Unet
from catalyst.dl.callbacks import DiceCallback, EarlyStoppingCallback, InferCallback, CheckpointCallback

# PyTorch made my work much much easier.
import segmentation_models_pytorch as smp
from dataloader import SegmentationDataset, SegmentationDatasetTest
from augmentations import get_training_augmentation, get_validation_augmentation, get_preprocessing

from metric import BCEDiceLoss, DiceLoss
device=torch.device('cuda')


def dice(img1, img2):
    img1 = np.asarray(img1).astype(np.bool)
    img2 = np.asarray(img2).astype(np.bool)

    intersection = np.logical_and(img1, img2)
    try:
        dice = 2. * intersection.sum() / (img1.sum() + img2.sum())
    except:
        return 0

def get_ids():
    train = pd.read_csv(f'input/train.csv')
    train['label'] = train['Image_Label'].apply(lambda x: x.split('_')[1])
    train['im_id'] = train['Image_Label'].apply(lambda x: x.split('_')[0])
    id_mask_count = train.loc[train['EncodedPixels'].isnull() == False, 'Image_Label'].apply(lambda x: x.split('_')[0]).value_counts().\
    reset_index().rename(columns={'index': 'img_id', 'Image_Label': 'count'})
    train_ids, valid_ids = train_test_split(id_mask_count['img_id'].values, random_state=42, stratify=id_mask_count['count'], test_size=0.1)
    return train_ids, valid_ids

def get_loaders(bs=32, num_workers=4, preprocessing_fn=None):
        train_dataset = SegmentationDataset(ids=train_ids,
                    transforms=get_training_augmentation(),
                    preprocessing=get_preprocessing(preprocessing_fn))
        valid_dataset = SegmentationDataset(ids=valid_ids,
                    transforms=get_validation_augmentation(),
                    preprocessing=get_preprocessing(preprocessing_fn))

        train_loader = DataLoader(train_dataset, batch_size=bs,
            shuffle=True, num_workers=num_workers)
        valid_loader = DataLoader(valid_dataset, batch_size=bs,
            shuffle=False, num_workers=num_workers)

        loaders = {
            "train": train_loader,
            "valid": valid_loader
        }
        return loaders

def get_model(encoder='resnet18', classes=4):
    encoder_weights = 'imagenet'
    model = smp.Unet(
        encoder_name=encoder,
        encoder_weights=encoder_weights,
        classes=classes,
        activation=None,
    )
    preprocessing_fn = smp.encoders.get_preprocessing_fn(encoder, encoder_weights)
    return model, preprocessing_fn



if __name__ == "__main__":
    wandb.init(project="segmentation-phase2")

    config = configparser.ConfigParser()
    config.read('configs/config.ini')
    conf = config['DEFAULT']

    lrd = conf.getfloat('lrd')
    lre = conf.getfloat('lre')
    bs = conf.getint('bs')
    epochs = conf.getint('epochs')
    num_workers = conf.getint('num_workers')
    encoder = conf.get('encoder')
    logdir = conf.get('logdir')
    bs = conf.getint('bs')

    train_ids, valid_ids = get_ids()
    model, preprocessing_fn = get_model(encoder)
    wandb.watch(model)
    loaders = get_loaders(bs, num_workers, preprocessing_fn)

    optimizer = torch.optim.Adam([
        {'params': model.decoder.parameters(), 'lr': lrd},
        {'params': model.encoder.parameters(), 'lr': lre},
    ])

    model.to(device)
    scheduler = ReduceLROnPlateau(optimizer, factor=0.5, patience=3)
    # criterion = smp.utils.losses.BCEDiceLoss(eps=1.)
    criterion = BCEDiceLoss()
    # criterion = DiceLoss() #Try this too
    runner = SupervisedRunner()

    # Train
    runner.train(
        model=model,
        criterion=criterion,
        optimizer=optimizer,
        scheduler=scheduler,
        loaders=loaders,
        callbacks=[DiceCallback(),
                   EarlyStoppingCallback(patience=5, min_delta=0.001)
                   ],
        logdir=logdir,
        num_epochs=epochs,
        verbose=True
    )
