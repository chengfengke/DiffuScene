"""Script used for parsing the 3D-FRONT data scenes into numpy files in order
to be able to avoid I/O overhead when training our model.
"""
import argparse
import logging
import json
import os
import sys

import numpy as np
from PIL import Image, ImageFilter
from tqdm import tqdm
#from scripts.utils import get_colored_objects_in_scene

from utils import DirLock, ensure_parent_directory_exists, \
    floor_plan_renderable, floor_plan_from_scene, \
    get_textured_objects_in_scene, scene_from_args, render, \
    get_colored_objects_in_scene

from scene_diffusion.datasets import filter_function
from scene_diffusion.datasets.threed_front import ThreedFront
from scene_diffusion.datasets.threed_front_dataset import \
    dataset_encoding_factory
import seaborn as sns
from scene_diffusion.datasets.threed_future_dataset import ThreedFutureNormPCDataset
 
 
def main(argv):
    parser = argparse.ArgumentParser(
        description="Prepare the 3D-FRONT scenes to train our model"
    )
    parser.add_argument(
        "output_directory",
        default="/tmp/",
        help="Path to output directory"
    )
    parser.add_argument(
        "path_to_3d_front_dataset_directory",
        help="Path to the 3D-FRONT dataset"
    )
    parser.add_argument(
        "path_to_3d_future_dataset_directory",
        help="Path to the 3D-FUTURE dataset"
    )
    parser.add_argument(
        "path_to_model_info",
        help="Path to the 3D-FUTURE model_info.json file"
    )
    parser.add_argument(
        "--path_to_floor_plan_textures",
        default="../demo/floor_plan_texture_images",
        help="Path to floor texture images"
    )
    parser.add_argument(
        "--path_to_invalid_scene_ids",
        default="../config/invalid_threed_front_rooms.txt",
        help="Path to invalid scenes"
    )
    parser.add_argument(
        "--path_to_invalid_bbox_jids",
        default="../config/black_list.txt",
        help="Path to objects that ae blacklisted"
    )
    parser.add_argument(
        "--annotation_file",
        default="../config/bedroom_threed_front_splits.csv",
        help="Path to the train/test splits file"
    )
    parser.add_argument(
        "--room_side",
        type=float,
        default=3.1,
        help="The size of the room along a side (default:3.1)"
    )
    parser.add_argument(
        "--dataset_filtering",
        default="threed_front_bedroom",
        choices=[
            "threed_front_bedroom",
            "threed_front_livingroom",
            "threed_front_diningroom",
            "threed_front_library"
        ],
        help="The type of dataset filtering to be used"
    )
    parser.add_argument(
        "--without_lamps",
        action="store_true",
        help="If set ignore lamps when rendering the room"
    )
    parser.add_argument(
        "--up_vector",
        type=lambda x: tuple(map(float, x.split(","))),
        default="0,0,-1",
        help="Up vector of the scene"
    )
    parser.add_argument(
        "--background",
        type=lambda x: list(map(float, x.split(","))),
        default="0,0,0,1",
        help="Set the background of the scene"
    )
    parser.add_argument(
        "--camera_target",
        type=lambda x: tuple(map(float, x.split(","))),
        default="0,0,0",
        help="Set the target for the camera"
    )
    parser.add_argument(
        "--camera_position",
        type=lambda x: tuple(map(float, x.split(","))),
        default="0,4,0",
        help="Camer position in the scene"
    )
    parser.add_argument(
        "--window_size",
        type=lambda x: tuple(map(int, x.split(","))),
        default="256,256",
        help="Define the size of the scene and the window"
    )
    parser.add_argument(
        "--no_texture",
        action="store_true",
        help="If set ignore lamps when rendering the room"
    )
    parser.add_argument(
        "--without_floor",
        action="store_true",
        help="if remove the floor plane"
    )
    # add objfeat
    parser.add_argument(
        "--add_objfeats",
        action="store_true",
        help="if remove the floor plane"
    )


    args = parser.parse_args(argv)
    logging.getLogger("trimesh").setLevel(logging.ERROR)

    # Check if output directory exists and if it doesn't create it
    if not os.path.exists(args.output_directory):
        os.makedirs(args.output_directory)

    # Create the scene and the behaviour list for simple-3dviz
    scene = scene_from_args(args)

    with open(args.path_to_invalid_scene_ids, "r") as f:
        invalid_scene_ids = set(l.strip() for l in f)

    with open(args.path_to_invalid_bbox_jids, "r") as f:
        invalid_bbox_jids = set(l.strip() for l in f)

    config = {
        "filter_fn":                 args.dataset_filtering,
        "min_n_boxes":               -1,
        "max_n_boxes":               -1,
        "path_to_invalid_scene_ids": args.path_to_invalid_scene_ids,
        "path_to_invalid_bbox_jids": args.path_to_invalid_bbox_jids,
        "annotation_file":           args.annotation_file
    }

    # Initially, we only consider the train split to compute the dataset
    # statistics, e.g the translations, sizes and angles bounds
    dataset = ThreedFront.from_dataset_directory(
        dataset_directory=args.path_to_3d_front_dataset_directory,
        path_to_model_info=args.path_to_model_info,
        path_to_models=args.path_to_3d_future_dataset_directory,
        filter_fn=filter_function(config, ["train", "val"], args.without_lamps)
    )
    print("Loading dataset with {} rooms".format(len(dataset)))

    # Compute the bounds for the translations, sizes and angles in the dataset.
    # This will then be used to properly align rooms.
    tr_bounds = dataset.bounds["translations"]
    si_bounds = dataset.bounds["sizes"]
    an_bounds = dataset.bounds["angles"]

    dataset_stats = {
        "bounds_translations": tr_bounds[0].tolist() + tr_bounds[1].tolist(),
        "bounds_sizes": si_bounds[0].tolist() + si_bounds[1].tolist(),
        "bounds_angles": an_bounds[0].tolist() + an_bounds[1].tolist(),
        "class_labels": dataset.class_labels,
        "object_types": dataset.object_types,
        "class_frequencies": dataset.class_frequencies,
        "class_order": dataset.class_order,
        "count_furniture": dataset.count_furniture
    }

    if args.add_objfeats:
        of_bounds = dataset.bounds["objfeats"]
        print([of_bounds[0], of_bounds[1], of_bounds[2]], type(of_bounds[0]), of_bounds[0].shape)
        dataset_stats["bounds_objfeats"] = of_bounds[0].tolist() + of_bounds[1].tolist() + of_bounds[2].tolist()
        print(of_bounds[0].tolist() + of_bounds[1].tolist() + of_bounds[2].tolist())
        print("add objfeats statistics: std {}, min {}, max {}".format(of_bounds[0], of_bounds[1], of_bounds[2]))

        of_bounds_32 = dataset.bounds["objfeats_32"]
        print([of_bounds_32[0], of_bounds_32[1], of_bounds_32[2]], type(of_bounds_32[0]), of_bounds_32[0].shape)
        dataset_stats["bounds_objfeats_32"] = of_bounds_32[0].tolist() + of_bounds_32[1].tolist() + of_bounds_32[2].tolist()
        print(of_bounds_32[0].tolist() + of_bounds_32[1].tolist() + of_bounds_32[2].tolist())
        print("add objfeats_32 statistics: std {}, min {}, max {}".format(of_bounds_32[0], of_bounds_32[1], of_bounds_32[2]))

    path_to_json = os.path.join(args.output_directory, "dataset_stats.txt")
    with open(path_to_json, "w") as f:
        json.dump(dataset_stats, f)
    print(
        "Saving training statistics for dataset with bounds: {} to {}".format(
            dataset.bounds, path_to_json
        )
    )

    dataset = ThreedFront.from_dataset_directory(
        dataset_directory=args.path_to_3d_front_dataset_directory,
        path_to_model_info=args.path_to_model_info,
        path_to_models=args.path_to_3d_future_dataset_directory,
        filter_fn=filter_function(
            config, ["train", "val", "test"], args.without_lamps
        )
    )
    print(dataset.bounds)
    print("Loading dataset with {} rooms".format(len(dataset)))

    encoded_dataset = dataset_encoding_factory(
        "basic", dataset, augmentations=None, box_ordering=None
    )

    for (i, es), ss in tqdm(zip(enumerate(encoded_dataset), dataset)):
        # Create a separate folder for each room
        room_directory = os.path.join(args.output_directory, ss.uid)
        # Check if room_directory exists and if it doesn't create it
        if os.path.exists(room_directory):
            continue

        # Make sure we are the only ones creating this file
        with DirLock(room_directory + ".lock") as lock:
            if not lock.is_acquired:
                continue
            if os.path.exists(room_directory):
                continue
            ensure_parent_directory_exists(room_directory)

            uids = [bi.model_uid for bi in ss.bboxes]
            jids = [bi.model_jid for bi in ss.bboxes]

            floor_plan_vertices, floor_plan_faces = ss.floor_plan

            # Render and save the room mask as an image
            room_mask = render(
                scene,
                [floor_plan_renderable(ss)],
                (1.0, 1.0, 1.0),
                "flat",
                os.path.join(room_directory, "room_mask.png")
            )[:, :, 0:1]

            if args.add_objfeats:
                np.savez_compressed(
                    os.path.join(room_directory, "boxes"),
                    uids=uids,
                    jids=jids,
                    scene_id=ss.scene_id,
                    scene_uid=ss.uid,
                    scene_type=ss.scene_type,
                    json_path=ss.json_path,
                    room_layout=room_mask,
                    floor_plan_vertices=floor_plan_vertices,
                    floor_plan_faces=floor_plan_faces,
                    floor_plan_centroid=ss.floor_plan_centroid,
                    class_labels=es["class_labels"],
                    translations=es["translations"],
                    sizes=es["sizes"],
                    angles=es["angles"],
                    objfeats=es["objfeats"],
                    objfeats_32=es["objfeats_32"],
                )
            else:
                np.savez_compressed(
                    os.path.join(room_directory, "boxes"),
                    uids=uids,
                    jids=jids,
                    scene_id=ss.scene_id,
                    scene_uid=ss.uid,
                    scene_type=ss.scene_type,
                    json_path=ss.json_path,
                    room_layout=room_mask,
                    floor_plan_vertices=floor_plan_vertices,
                    floor_plan_faces=floor_plan_faces,
                    floor_plan_centroid=ss.floor_plan_centroid,
                    class_labels=es["class_labels"],
                    translations=es["translations"],
                    sizes=es["sizes"],
                    angles=es["angles"]
                )


            if args.no_texture:
                # Render a top-down orthographic projection of the room at a
                # specific pixel resolutin
                path_to_image = "{}/rendered_scene_notexture_{}.png".format(
                    room_directory, args.window_size[0]
                )
                if os.path.exists(path_to_image):
                    continue
                
                floor_plan, _, _ = floor_plan_from_scene(
                    ss, args.path_to_floor_plan_textures, without_room_mask=True, no_texture=True,
                )
                # read class labels and get the color map of each class
                class_labels = es["class_labels"]
                color_palette = np.array(sns.color_palette('hls', class_labels.shape[1]-2))
                class_index = class_labels.argmax(axis=1)
                cc = color_palette[class_index, :]
                print('class_labels :', class_labels.shape)
                renderables = get_colored_objects_in_scene(
                    ss, cc, ignore_lamps=args.without_lamps
                )
            else:
                # Render a top-down orthographic projection of the room at a
                # specific pixel resolutin
                path_to_image = "{}/rendered_scene_{}.png".format(
                    room_directory, args.window_size[0]
                )
                if os.path.exists(path_to_image):
                    continue

                # Get a simple_3dviz Mesh of the floor plan to be rendered
                floor_plan, _, _ = floor_plan_from_scene(
                    ss, args.path_to_floor_plan_textures, without_room_mask=True, no_texture=False,
                )
                renderables = get_textured_objects_in_scene(
                    ss, ignore_lamps=args.without_lamps
                )

            if args.without_floor:
                render(
                    scene,
                    renderables,
                    color=None,
                    mode="shading",
                    frame_path=path_to_image
                )
            else:
                render(
                    scene,
                    renderables + floor_plan,
                    color=None,
                    mode="shading",
                    frame_path=path_to_image
                )



if __name__ == "__main__":
    main(sys.argv[1:])