from typing import List

import cv2
import torch
from torch import nn
import numpy as np
from PIL import Image
from surya.postprocessing.heatmap import get_and_clean_boxes
from surya.postprocessing.affinity import get_vertical_lines, get_horizontal_lines
from surya.model.processing import prepare_image, split_image
from surya.settings import settings


def batch_inference(images: List, model, processor):
    assert all([isinstance(image, Image.Image) for image in images])

    images = [image.copy().convert("RGB") for image in images]
    orig_sizes = [image.size for image in images]
    split_index = []
    split_heights = []
    image_splits = []
    for i, image in enumerate(images):
        image_parts, split_height = split_image(image, processor)
        image_splits.extend(image_parts)
        split_index.extend([i] * len(image_parts))
        split_heights.extend(split_height)

    image_splits = [prepare_image(image, processor) for image in image_splits]

    pred_parts = []
    for i in range(0, len(image_splits), settings.BATCH_SIZE):
        batch = image_splits[i:i+settings.BATCH_SIZE]
        # Batch images in dim 0
        batch = torch.stack(batch, dim=0)
        batch = batch.to(model.dtype)
        batch = batch.to(model.device)

        with torch.inference_mode():
            pred = model(pixel_values=batch)

        logits = pred.logits
        if logits.shape[-2:] != batch.shape[-2:]:
            # Upsample logits to orig size if needed
            logits = nn.functional.interpolate(
                logits,
                size=batch.shape[-2:],
                mode="bilinear",
                align_corners=False,
            )

        for j in range(logits.shape[0]):
            heatmap = logits[j, 0, :, :].detach().cpu().numpy()
            affinity_map = logits[j, 1, :, :].detach().cpu().numpy()

            pred_parts.append((heatmap, affinity_map))

    preds = []
    for i, (idx, height) in enumerate(zip(split_index, split_heights)):
        if len(preds) <= idx:
            preds.append(pred_parts[i])
        else:
            heatmap, affinity_map = preds[idx]
            pred_heatmap = pred_parts[i][0]
            pred_affinity = pred_parts[i][1]

            if height != processor.size["height"]:
                # Cut off padding to get original height
                pred_heatmap = pred_heatmap[:height, :]
                pred_affinity = pred_affinity[:height, :]

            heatmap = np.vstack([heatmap, pred_heatmap])
            affinity_map = np.vstack([affinity_map, pred_affinity])
            preds[idx] = (heatmap, affinity_map)

    assert len(preds) == len(images)
    results = []
    for i in range(len(images)):
        heatmap, affinity_map = preds[i]
        heat_img = Image.fromarray((heatmap * 255).astype(np.uint8))
        aff_img = Image.fromarray((affinity_map * 255).astype(np.uint8))

        affinity_size = list(reversed(affinity_map.shape))
        heatmap_size = list(reversed(heatmap.shape))
        bboxes = get_and_clean_boxes(heatmap, heatmap_size, orig_sizes[i])
        vertical_lines = get_vertical_lines(affinity_map, affinity_size, orig_sizes[i])
        horizontal_lines = get_horizontal_lines(affinity_map, affinity_size, orig_sizes[i])

        results.append({
            "bboxes": bboxes,
            "vertical_lines": vertical_lines,
            "horizontal_lines": horizontal_lines,
            "heatmap": heat_img,
            "affinity_map": aff_img,
        })

    return results





