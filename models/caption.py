import torch
from torch import nn
import torch.nn.functional as F

from .utils import NestedTensor, nested_tensor_from_tensor_list, ensure_unmasked_values
from .backbone import build_backbone
from .ConcatTransformer import build_transformer as build_concat_transformer


class Caption(nn.Module):

    def __init__(self, backbone, transformer, positional_encoding, hidden_dim,
                 vocab_size):
        super().__init__()
        self.backbone = backbone
        self.positional_encoding = positional_encoding
        self.input_proj = nn.Conv2d(in_channels=backbone.num_channels,
                                    out_channels=hidden_dim,
                                    kernel_size=1)
        self.transformer = transformer
        self.mlp = MLP(hidden_dim, 512, vocab_size, 3)

    def forward(self, samples, target_exp, target_exp_mask, return_attention=False):

        # target features

        if not isinstance(samples, NestedTensor):
            samples = nested_tensor_from_tensor_list(samples)

        features = self.backbone(samples)['0']
        src, mask = features.decompose()
        src = self.input_proj(src)
        assert mask is not None
        # flatten vectors
        src = src.flatten(2)
        mask = mask.flatten(1)

        hs, att = self.transformer(
            src_t=src, mask_t=mask, 
            src_c=None, mask_c=None, 
            tgt=target_exp, tgt_mask=target_exp_mask)
        out = self.mlp(hs.permute(1, 0, 2))

        if return_attention:
            return out, att
        
        return out
    

class CaptionLoc(nn.Module):

    def __init__(self, backbone, transformer, positional_encoding, hidden_dim,
                 vocab_size):
        super().__init__()
        self.backbone = backbone
        self.positional_encoding = positional_encoding
        self.input_proj = nn.Conv2d(in_channels=backbone.num_channels,
                                    out_channels=hidden_dim,
                                    kernel_size=1)
        self.loc_proj = nn.Linear(7, hidden_dim)
        self.transformer = transformer
        self.mlp = MLP(hidden_dim, 512, vocab_size, 3)

    def forward(self, t_samples, loc_feats, target_exp, target_exp_mask, return_attention=False):

        # target features
        if not isinstance(t_samples, NestedTensor):
            t_samples = nested_tensor_from_tensor_list(t_samples)
        t_features = self.backbone(t_samples)['0']
        t_src, t_mask = t_features.decompose()
        t_src = self.input_proj(t_src)
        assert t_mask is not None
        # flatten vectors
        t_src = t_src.flatten(2)
        t_mask = t_mask.flatten(1)

        # location features
        loc_src = self.loc_proj(loc_feats).unsqueeze(-1)
        loc_masks = torch.zeros(
            (loc_feats.shape[0], 1)).bool().to(t_mask.device)

        # concatenate target and location to target vector
        src = torch.concat([t_src, loc_src], 2)
        mask = torch.concat([t_mask, loc_masks], 1)

        hs, att = self.transformer(
            src_t=src, mask_t=mask, 
            src_c=None, mask_c=None, 
            tgt=target_exp, tgt_mask=target_exp_mask)
        out = self.mlp(hs.permute(1, 0, 2))

        if return_attention:
            return out, att
        
        return out
    

class CaptionGlobalLoc(nn.Module):

    def __init__(self, backbone, transformer, positional_encoding, hidden_dim,
                 vocab_size):
        super().__init__()
        self.backbone = backbone
        self.positional_encoding = positional_encoding
        self.input_proj = nn.Conv2d(in_channels=backbone.num_channels,
                                    out_channels=hidden_dim,
                                    kernel_size=1)
        self.loc_proj = nn.Linear(1, hidden_dim)
        self.transformer = transformer
        self.mlp = MLP(hidden_dim, 512, vocab_size, 3)

    def forward(self, t_samples, g_samples, loc_feats, target_exp, target_exp_mask, return_attention=False):

        # target features
        if not isinstance(t_samples, NestedTensor):
            t_samples = nested_tensor_from_tensor_list(t_samples)
        t_features = self.backbone(t_samples)['0']
        t_src, t_mask = t_features.decompose()
        t_src = self.input_proj(t_src)
        assert t_mask is not None
        # flatten vectors
        t_src = t_src.flatten(2)  # [b, hidden_dim, len]
        t_mask = t_mask.flatten(1)  # [b, len]

        # location features
        loc_src = loc_feats.unsqueeze(2) # [b, n_feats] -> [b, n_feats, 1]
        loc_src = self.loc_proj(loc_src)  # [b, n_feats, hidden_dim]
        loc_src = loc_src.permute(0,2,1)  # [b, hidden_dim, n_feats]
        loc_masks = torch.zeros(
            (loc_src.shape[0], loc_src.shape[2])).bool().to(t_mask.device)

        # concatenate target and location to target vector
        target_src = torch.concat([t_src, loc_src], 2)
        target_mask = torch.concat([t_mask, loc_masks], 1)

        # global features
        if not isinstance(g_samples, NestedTensor):
            g_samples = nested_tensor_from_tensor_list(g_samples)
        g_features = self.backbone(g_samples)['0']
        g_src, g_mask = g_features.decompose()
        g_src = self.input_proj(g_src)
        assert g_mask is not None
        # ensure there are unmasked context values
        g_mask = ensure_unmasked_values(g_mask)
        # flatten vectors
        g_src = g_src.flatten(2)  # [b, hidden_dim, len]
        g_mask = g_mask.flatten(1)  # [b, len]

        hs, att = self.transformer(
            src_t=target_src, mask_t=target_mask, 
            src_c=g_src, mask_c=g_mask, 
            tgt=target_exp, tgt_mask=target_exp_mask)
        out = self.mlp(hs.permute(1, 0, 2))
        
        if return_attention:
            return out, att
        
        return out
    

class MLP(nn.Module):
    """ Very simple multi-layer perceptron (also called FFN)"""

    def __init__(self, input_dim, hidden_dim, output_dim, num_layers):
        super().__init__()
        self.num_layers = num_layers
        h = [hidden_dim] * (num_layers - 1)
        self.layers = nn.ModuleList(
            nn.Linear(n, k) for n, k in zip([input_dim] + h, h + [output_dim]))

    def forward(self, x):
        for i, layer in enumerate(self.layers):
            x = F.relu(layer(x)) if i < self.num_layers - 1 else layer(x)
        return x


def build_model(config):
    
    backbone = build_backbone(config)
    
    transformer = build_concat_transformer(config)

    use_global = config.use_global_features
    use_location = config.use_location_features

    print(f'global features: {use_global}, location features: {use_location}')

    # pick model class
    if not use_global and not use_location:
        # default / no global image or loc features
        Model = Caption
    elif not use_global and use_location:
        # loc features
        Model = CaptionLoc
    elif use_global and use_location:
        # global features + loc features
        Model = CaptionGlobalLoc
    else:
        raise NotImplementedError()
    
    # init model
    model = Model(backbone, 
                  transformer, 
                  None, 
                  config.hidden_dim, 
                  config.vocab_size)
    
    print(f'Built {model.__class__.__name__} model with {transformer.__class__.__name__}')

    criterion = torch.nn.CrossEntropyLoss()

    return model, criterion
