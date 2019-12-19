"""
Evaluates a folder of video files or a single file with a xception binary
classification network.

Usage:
python detect_from_video.py
    -i <folder with video files or path to video file>
    -m <path to model file>
    -o <path to output folder, will write one or multiple output videos there>

Author: Andreas Rössler
"""
import os
import argparse
from os.path import join
import cv2
import dlib
import torch
import torch.nn as nn
from PIL import Image as pil_image
from tqdm import tqdm
from pdb import set_trace
from network.models import model_selection
from dataset.transform import xception_default_data_transforms


def get_boundingbox(face, width, height, scale=1.3, minsize=None):
    """
    Expects a dlib face to generate a quadratic bounding box.
    :param face: dlib face class
    :param width: frame width
    :param height: frame height
    :param scale: bounding box size multiplier to get a bigger face region
    :param minsize: set minimum bounding box size
    :return: x, y, bounding_box_size in opencv form
    """
    x1 = face.left()
    y1 = face.top()
    x2 = face.right()
    y2 = face.bottom()
    size_bb = int(max(x2 - x1, y2 - y1) * scale)
    if minsize:
        if size_bb < minsize:
            size_bb = minsize
    center_x, center_y = (x1 + x2) // 2, (y1 + y2) // 2

    # Check for out of bounds, x-y top left corner
    x1 = max(int(center_x - size_bb // 2), 0)
    y1 = max(int(center_y - size_bb // 2), 0)
    # Check for too big bb size for given x, y
    size_bb = min(width - x1, size_bb)
    size_bb = min(height - y1, size_bb)

    return x1, y1, size_bb


def preprocess_image(image, cuda=True):
    """
    Preprocesses the image such that it can be fed into our network.
    During this process we envoke PIL to cast it into a PIL image.

    :param image: numpy image in opencv form (i.e., BGR and of shape
    :return: pytorch tensor of shape [1, 3, image_size, image_size], not
    necessarily casted to cuda
    """
    # Revert from BGR
    image = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
    # Preprocess using the preprocessing function used during training and
    # casting it to PIL image
    preprocess = xception_default_data_transforms['test']
    preprocessed_image = preprocess(pil_image.fromarray(image))
    # Add first dimension as the network expects a batch
    preprocessed_image = preprocessed_image.unsqueeze(0)
    if cuda:
        preprocessed_image = preprocessed_image.cuda()
    return preprocessed_image


def predict_with_model(image, model, post_function=nn.Softmax(dim=1),
                       cuda=True):
    """
    Predicts the label of an input image. Preprocesses the input image and
    casts it to cuda if required

    :param image: numpy image
    :param model: torch model with linear layer at the end
    :param post_function: e.g., softmax
    :param cuda: enables cuda, must be the same parameter as the model
    :return: prediction (1 = fake, 0 = real)
    """
    # Preprocess
    preprocessed_image = preprocess_image(image, cuda)

    # Model prediction
    output = model(preprocessed_image)
    output = post_function(output)

    # Cast to desired
    _, prediction = torch.max(output, 1)    # argmax
    prediction = float(prediction.cpu().numpy())

    return int(prediction), output

def test_full_image_network(video_folder_path, model_path, output_path, fns,
                            start_frame=0, end_frame=None, cuda=True):
    """
    Reads a video and evaluates a subset of frames with the a detection network
    that takes in a full frame. Outputs are only given if a face is present
    and the face is highlighted using dlib.
    :param video_path: path to video file
    :param model_path: path to model file (should expect the full sized image)
    :param output_path: path where the output video is stored
    :param start_frame: first frame to evaluate
    :param end_frame: last frame to evaluate
    :param cuda: enable cuda
    :return:
    """
    preds = {}
    os.makedirs(output_path, exist_ok=True)
    # Load pretrained model
    model = torch.load(model_path)
    print('Model found in {}'.format(model_path))
    if cuda: model = model.cuda()

    for fn in fns: 
        start_frame=0
        video_path = f"{video_folder_path}/{fn}"
        print('Starting: {}'.format(video_path))
        # Read and write
        reader = cv2.VideoCapture(video_path)
        fps = reader.get(cv2.CAP_PROP_FPS)
        video_fn = video_path.split('/')[-1]
        print(f"Currently predicting for {video_fn}")
        num_frames = int(reader.get(cv2.CAP_PROP_FRAME_COUNT))
        writer = None
        # Face detector
        face_detector = dlib.get_frontal_face_detector()
        # Frame numbers and length of output video
        frame_num = 0
        assert start_frame < num_frames - 1
        end_frame = end_frame if end_frame else num_frames
        pbar = tqdm(total=end_frame-start_frame)
        labels={"fake":0, "real":0}
        while reader.isOpened():
            _, image = reader.read()
            if image is None: break
            frame_num += 1
            if frame_num < start_frame: continue
            pbar.update(1)
            # Image size
            height, width = image.shape[:2]
            # 2. Detect with dlib
            gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
            faces = face_detector(gray, 1)
            if len(faces):
                face = faces[0]
                x, y, size = get_boundingbox(face, width, height)
                cropped_face = image[y:y+size, x:x+size]
                prediction, output = predict_with_model(cropped_face, model,
                                                        cuda=cuda)
                x = face.left()
                y = face.top()
                w = face.right() - x
                h = face.bottom() - y
                label = 'fake' if prediction == 1 else 'real'
                labels[label]+=1
                if label == "fake" and (labels["fake"]/num_frames)>0.2:
                    preds[video_fn]=output.max()
                elif label == "real" and (labels["real"]/num_frames)>0.75: 
                    preds[video_fn]=output.max()
            color = (0, 255, 0) if prediction == 0 else (0, 0, 255)
        if frame_num >= end_frame:
            break
    pbar.close()
    return preds

if __name__ == '__main__':
    p = argparse.ArgumentParser(
        formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    p.add_argument('--video_path', '-i', type=str)
    p.add_argument('--model_path', '-mi', type=str, default=None)
    p.add_argument('--output_path', '-o', type=str,
                   default='.')
    p.add_argument('--start_frame', type=int, default=0)
    p.add_argument('--end_frame', type=int, default=None)
    p.add_argument('--cuda', action='store_true', default=True)
    args = p.parse_args()

    video_path = args.video_path
    if video_path.endswith('.mp4') or video_path.endswith('.avi'):
        test_full_image_network(**vars(args))
    else:
        videos = os.listdir(video_path)
        for video in videos:
            args.video_path = join(video_path, video)
            test_full_image_network(**vars(args))
