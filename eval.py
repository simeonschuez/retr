import torch
from torch.utils.data import DataLoader
import argparse
from models import caption
from datasets import refcoco
from configuration import Config
import os
import json

from eval_utils.decode import prepare_tokenizer, load_image, greedy
from engine import eval_model


def prepare_model(args, config):

    # load model
    assert args.checkpoint is not None

    if args.override_config:
        # overriding config settings with parameters given by checkpoint
        override_config_with_checkpoint(args.checkpoint, config)

    if not os.path.exists(args.checkpoint):
        raise NotImplementedError("Give valid checkpoint path")
    else:
        model, _ = caption.build_model(config)
        checkpoint = torch.load(args.checkpoint, map_location="cpu")
        model.load_state_dict(checkpoint["model_state_dict"])

    return model


def setup_val_dataloader(config):
    dataset_val = refcoco.build_dataset(
        config, 
        mode="validation", 
        return_unique=True)
    sampler_val = torch.utils.data.SequentialSampler(dataset_val)
    data_loader_val = DataLoader(
        dataset_val,
        batch_size=config.batch_size,
        sampler=sampler_val,
        drop_last=False,
        num_workers=config.num_workers,
    )
    return data_loader_val


def override_config_with_checkpoint(checkpoint, config):
    use_glob = config.use_global_features
    use_loc = config.use_location_features
    
    if 'loc_checkpoint' in checkpoint:
        if not (not use_glob and use_loc):
            # override settings
            config.use_global_features = False
            config.use_location_features = True
            # send warning
            print(f'''CAUTION: Overriding configuration!
                WAS: use_global_features=={use_glob}; use_location_features=={use_loc}
                NEW: use_global_features=={config.use_global_features}; use_location_features=={config.use_location_features}
                ''')
            
    elif 'loc_glob_checkpoint' in checkpoint:
        if not (use_glob and use_loc):
            # override settings
            config.use_global_features = True
            config.use_location_features = True
            # send warning
            print(f'''CAUTION: Overriding configuration!
                WAS: use_global_features=={use_glob}; use_location_features=={use_loc}
                NEW: use_global_features=={config.use_global_features}; use_location_features=={config.use_location_features}
                ''')
            
    else:
        raise NotImplementedError(
            "Overriding model checkpoints is not supported for the model type given by the checkpoint"
        )


def main_image(args, config):

    assert args.path is not None
    image_path = args.path

    # model
    model = prepare_model(args, config).to(args.device)

    # tokenizer
    tokenizer, start_token, end_token = prepare_tokenizer()
    bos_id = tokenizer.convert_tokens_to_ids(tokenizer.cls_token)
    eos_id = tokenizer.convert_tokens_to_ids(tokenizer.sep_token)

    # image handling
    image = load_image(image_path, transform=refcoco.val_transform)

    # decoding
    output_ids = greedy(
        image,
        model,
        max_len=config.max_position_embeddings,
        device="auto",
        bos_token=bos_id,
        eos_token=eos_id,
    )

    output = tokenizer.decode(output_ids[0], skip_special_tokens=True)

    return output


def main_val_set(args, config):

    # model
    model = prepare_model(args, config).to(args.device)

    # tokenizer
    tokenizer, _, _ = prepare_tokenizer()

    data_loader = setup_val_dataloader(config)

    metrics, generated = eval_model(
        model, data_loader, tokenizer, config, print_samples=args.print_samples
    )

    return metrics, generated


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="REG")

    parser.add_argument("--mode", default="val")
    parser.add_argument(
        "--split", type=str.lower, choices=["val", "testa", "testb"], default="val"
    )
    parser.add_argument("--path", type=str, help="path to image", default=None)
    parser.add_argument("--checkpoint", type=str, help="checkpoint path", default=None)
    parser.add_argument(
        "--device", type=str.lower, choices=["cuda", "cpu", "auto"], default="auto"
    )
    parser.add_argument("--print_samples", action="store_true")
    parser.add_argument("--store_results", action="store_true")
    parser.add_argument("--override_config", action="store_true")
    args = parser.parse_args()

    if args.device == "auto":
        args.device = "cuda" if torch.cuda.is_available() else "cpu"

    config = Config()

    if args.mode == "val":

        metrics, generated = main_val_set(args, config)

        print(metrics)

        if args.store_results:
            assert args.checkpoint is not None
            model_name = os.path.split(args.checkpoint)[-1]
            outdir = os.path.abspath("./data/results")
            if not os.path.isdir(outdir):
                print(f"create output directory {outdir}")
                os.makedirs(outdir)
            # generated expressions
            outfile_name = model_name.replace(".pth", f"_{args.split}_generated.json")
            outfile_path = os.path.join(outdir, outfile_name)
            print(f"write generated expressions to {outfile_path}")
            with open(outfile_path, "w") as f:
                json.dump(generated, f)
            # evaluation results
            outfile_name = model_name.replace(".pth", f"_{args.split}_eval.json")
            outfile_path = os.path.join(outdir, outfile_name)
            print(f"write evaluation results to {outfile_path}")
            with open(outfile_path, "w") as f:
                json.dump(metrics, f)

    elif args.mode == "image":

        print(main_image(args, config))
