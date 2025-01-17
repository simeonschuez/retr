import torch
from torch.utils.data import DataLoader

import numpy as np
import os

from models import utils, caption
from data_utils import refcoco
from configuration import Config
from engine import train_one_epoch, evaluate, eval_model
from train_utils.checkpoints import save_ckp
from eval_utils.decode import prepare_tokenizer


def main(config):
    device = torch.device(config.device)
    print(f'Initializing Device: {device}')

    seed = config.seed + utils.get_rank()
    torch.manual_seed(seed)
    np.random.seed(seed)

    model, criterion = caption.build_model(config)
    model.to(device)

    n_parameters = sum(p.numel()
                       for p in model.parameters() if p.requires_grad)
    print(f"Number of params: {n_parameters}")

    param_dicts = [
        {"params": [p for n, p in model.named_parameters(
        ) if "backbone" not in n and p.requires_grad]},
        {
            "params": [p for n, p in model.named_parameters() if "backbone" in n and p.requires_grad],
            "lr": config.lr_backbone,
        },
    ]
    optimizer = torch.optim.AdamW(
        param_dicts, lr=config.lr, weight_decay=config.weight_decay)
    lr_scheduler = torch.optim.lr_scheduler.StepLR(optimizer, config.lr_drop)
    tokenizer, _, _ = prepare_tokenizer()

    dataset_train = refcoco.build_dataset(config, mode='training')
    dataset_val = refcoco.build_dataset(config, mode='validation')
    dataset_cider = refcoco.build_dataset(
        config, mode='validation', return_unique=True)
    print(f"Train: {len(dataset_train)}")
    print(f"Valid: {len(dataset_val)}")
    print(f"CIDEr evaluation: {len(dataset_cider)}")

    sampler_train = torch.utils.data.RandomSampler(dataset_train)
    sampler_val = torch.utils.data.SequentialSampler(dataset_val)
    sampler_cider = torch.utils.data.SequentialSampler(dataset_cider)

    batch_sampler_train = torch.utils.data.BatchSampler(
        sampler_train, config.batch_size, drop_last=True
    )

    data_loader_train = DataLoader(
        dataset_train, batch_sampler=batch_sampler_train, num_workers=config.num_workers)
    data_loader_val = DataLoader(dataset_val, config.batch_size,
                                 sampler=sampler_val, drop_last=False, num_workers=config.num_workers)
    data_loader_cider = DataLoader(dataset_cider, config.batch_size,
                                 sampler=sampler_cider, drop_last=False, num_workers=config.num_workers)

    if not os.path.exists(config.checkpoint_path):
        os.mkdir(config.checkpoint_path)
    
    loc_used = '_loc' if config.use_location_features else ''
    glob_used = '_glob' if config.use_global_features else ''
    cpt_template = f'{config.transformer_type}_{config.prefix}{loc_used}{glob_used}_checkpoint_#.pth'

    print("Start Training..")
    cider_scores = [0]
    for epoch in range(config.start_epoch, config.epochs):
        print(f"Epoch: {epoch}")
        epoch_loss = train_one_epoch(
            model, criterion, data_loader_train, optimizer, device, epoch, config.clip_max_norm)
        lr_scheduler.step()
        print(f"Training Loss: {epoch_loss}")

        validation_loss = evaluate(model, criterion, data_loader_val, device)
        print(f"Validation Loss: {validation_loss}")

        eval_results, _ = eval_model(model, data_loader_cider, tokenizer, config)
        cider_score = eval_results['CIDEr']
        print(f"CIDEr score: {cider_score}")

        checkpoint_name = cpt_template.replace('#', str(epoch))
        save_ckp(
            epoch, model, optimizer, lr_scheduler, 
            train_loss=epoch_loss, val_loss=validation_loss, cider_score=cider_score,
            path=os.path.join(config.checkpoint_path, checkpoint_name)
        )
        
        if config.early_stopping:
            if cider_score < min(cider_scores[-5:]):
                print('no improvements within the last 5 epochs -- early stopping triggered!')
                break

        cider_scores.append(cider_score)

        print()


if __name__ == "__main__":
    config = Config()
    main(config)
