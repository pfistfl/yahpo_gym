import typing as ty

import torch
import torch.nn as nn
import torch.nn.functional as F
import torch.nn.init as nn_init

from fastai.tabular.all import *

from yahpo_train.cont_scalers import *
from yahpo_train.models_utils import *


class AbstractSurrogate(nn.Module):
    def __init__(self):
        super().__init__()

    def _build_embeddings(
        self, dls, embds_dbl=None, embds_tgt=None, emb_szs=None, instance_names=None
    ):
        self._build_embeddings_xcont(dls=dls, embds_dbl=embds_dbl)
        self._build_embeddings_y(
            dls=dls, embds_tgt=embds_tgt, instance_names=instance_names
        )
        self._build_embeddings_xcat(dls=dls, emb_szs=emb_szs)
        self.n_inputs = self.n_emb + self.n_cont

    def _build_embeddings_xcont(self, dls, embds_dbl):
        if embds_dbl is not None:
            self.embds_dbl = nn.ModuleList(
                [
                    f(torch.from_numpy(cont[1].values).float())
                    for cont, f in zip(dls.all_cols[dls.cont_names].items(), embds_dbl)
                ]
            )
        else:
            self.embds_dbl = nn.ModuleList(
                [
                    ContTransformerStandardize(torch.from_numpy(cont.values).float())
                    for _, cont in dls.all_cols[dls.cont_names].items()
                ]
            )
        self.n_cont = len(dls.cont_names)

    def _build_embeddings_y(self, dls, embds_tgt=None, instance_names=None):
        if embds_tgt is not None:
            if instance_names is not None:
                self.embds_tgt = nn.ModuleList(
                    [
                        f(
                            torch.from_numpy(cont[1].values).float(),
                            group=torch.from_numpy(dls.xs[instance_names].values).int(),
                        )
                        for cont, f in zip(dls.ys[dls.y_names].items(), embds_tgt)
                    ]
                )
            else:
                self.embds_tgt = nn.ModuleList(
                    [
                        f(torch.from_numpy(cont[1].values).float())
                        for cont, f in zip(dls.ys[dls.y_names].items(), embds_tgt)
                    ]
                )
        else:
            if instance_names is not None:
                self.embds_tgt = nn.ModuleList(
                    [
                        ContTransformerStandardizeGroupedRange(
                            torch.from_numpy(cont.values).float(),
                            group=torch.from_numpy(dls.xs[instance_names].values).int(),
                        )
                        for name, cont in dls.ys[dls.y_names].items()
                    ]
                )
            else:
                self.embds_tgt = nn.ModuleList(
                    [
                        ContTransformerStandardizeRange(
                            torch.from_numpy(cont.values).float()
                        )
                        for name, cont in dls.ys[dls.y_names].items()
                    ]
                )

    def _build_embeddings_xcat(self, dls, emb_szs):
        # Categorical Embeddings
        emb_szs = get_emb_sz(dls.train_ds, {} if emb_szs is None else emb_szs)
        self.embds_fct = nn.ModuleList([Embedding(ni, nf) for ni, nf in emb_szs])
        # init with Kaiming
        [
            nn_init.kaiming_uniform_(embd.weight, a=math.sqrt(5))
            for embd in self.embds_fct
        ]
        self.n_emb = sum(e.embedding_dim for e in self.embds_fct)

    def _embed_features(self, x_cat, x_cont):
        if self.n_emb != 0:
            x = [e(x_cat[:, i]) for i, e in enumerate(self.embds_fct)]
            x = torch.cat(x, 1)
        if self.n_cont != 0:
            xd = [e(x_cont[:, i]).unsqueeze(1) for i, e in enumerate(self.embds_dbl)]
            xd = torch.cat(xd, 1)
            x = torch.cat([x, xd], 1) if self.n_emb > 0 else xd
        return x

    def trafo_ys(self, ys, group=None):
        ys = [
            e(ys[:, i], group=group).unsqueeze(1) for i, e in enumerate(self.embds_tgt)
        ]
        ys = torch.cat(ys, 1)
        return ys

    def inv_trafo_ys(self, ys, group=None):
        ys = [
            e.invert(ys[:, i], group=group).unsqueeze(1)
            for i, e in enumerate(self.embds_tgt)
        ]
        ys = torch.cat(ys, 1)
        return ys

    def forward(self):
        raise NotImplementedError

    def export_onnx(self, config_dict, device="cuda:0", suffix=""):
        """
        Export model to an ONNX file.
        We can safely ignore tracing errors with respect to lambda since lambda will be constant during inference.
        """
        self.eval()
        model_path = config_dict.get_path("model")
        if suffix != "":
            model_path = config_dict.get_path("model").replace(
                ".onnx", "_" + suffix + ".onnx"
            )
        torch.onnx.export(
            self,
            # touple of x_cat followed by x_cont
            (
                torch.ones(
                    1, len(config_dict.cat_names), dtype=torch.int, device=device
                ),
                torch.randn(1, len(config_dict.cont_names), device=device),
            ),
            model_path,
            do_constant_folding=True,
            export_params=True,
            input_names=["x_cat", "x_cont"],
            output_names=["output"],
            opset_version=13,
            # dynamic axes allow us to do batch prediction
            dynamic_axes={
                "x_cat": {0: "batch_size"},
                "x_cont": {0: "batch_size"},
                "output": {0: "batch_size"},
            },
        )


class Attention(nn.Module):
    def __init__(self, n_inputs):
        super().__init__()
        self.n_inputs = n_inputs
        self.attn = nn.Linear(n_inputs, 1)

    def forward(self, x):
        attn_weights = self.attn(x)
        attn_weights = F.softmax(attn_weights, dim=0)
        weighted_x = attn_weights * x
        return weighted_x


# ResNet
class ResNet(AbstractSurrogate):
    def __init__(
        self,
        dls,
        embds_dbl=None,
        embds_tgt=None,
        emb_szs=None,
        instance_names=None,
        d: int = 256,
        d_hidden_factor: float = 2,
        n_layers: int = 4,
        activation: str = "reglu",
        normalization: str = "batchnorm",
        hidden_dropout: float = 0.0,
        residual_dropout: float = 0.2,
        final_act=nn.Sigmoid(),
    ) -> None:
        """
        ResNet model.
        Repurposed and adapted from https://github.com/yandex-research/rtdl under Apache License 2.0
        dls :: DatasetLoader
        embds_dbl :: Numeric Embeddings
        embds_tgt :: Target Embeddings
        embd_szs :: Embedding sizes
        instance_names :: names of the instances id
        d :: dimensionality of the hidden space
        d_hidden_factor :: factor by which the hidden dimension is reduced
        n_layers :: number of layers
        activation :: activation function
        normalization :: normalization function
        hidden_dropout :: dropout rate for hidden layers
        residual_dropout :: dropout rate for residual connections
        final_act :: final activation function
        """
        super().__init__()

        self.instance_names = instance_names

        self._build_embeddings(
            dls=dls,
            embds_dbl=embds_dbl,
            embds_tgt=embds_tgt,
            emb_szs=emb_szs,
            instance_names=instance_names,
        )

        self.main_activation = get_activation_fn(activation)
        self.last_activation = get_nonglu_activation_fn(activation)
        self.residual_dropout = residual_dropout
        self.hidden_dropout = hidden_dropout
        self.final_act = final_act

        d_hidden = int(d * d_hidden_factor)
        self.first_layer = nn.Linear(self.n_inputs, d)
        self.layers = nn.ModuleList(
            [
                nn.ModuleDict(
                    {
                        "norm": (self._make_normalization(normalization)(d)),
                        "linear0": nn.Linear(
                            d, d_hidden * (2 if activation.endswith("glu") else 1)
                        ),
                        "linear1": nn.Linear(d_hidden, d),
                    }
                )
                for _ in range(n_layers)
            ]
        )
        self.last_normalization = self._make_normalization(normalization)(d)
        self.head = nn.Linear(d, dls.ys.shape[1])

    def forward(self, x_cat, x_cont, invert_ytrafo=True) -> Tensor:
        x = self._embed_features(x_cat, x_cont)
        x = self.first_layer(x)
        for layer in self.layers:
            layer = ty.cast(ty.Dict[str, nn.Module], layer)
            z = x
            z = layer["norm"](z)
            z = layer["linear0"](z)
            z = self.main_activation(z)
            if self.hidden_dropout:
                z = F.dropout(z, self.hidden_dropout, self.training)
            z = layer["linear1"](z)
            if self.residual_dropout:
                z = F.dropout(z, self.residual_dropout, self.training)
            x = x + z
        x = self.last_normalization(x)
        x = self.last_activation(x)
        x = self.head(x)
        y = self.final_act(x)
        if torch.tensor(invert_ytrafo):
            if self.instance_names is not None:
                current_device = y.device
                group = x_cat[:, 0].to(current_device)
                return self.inv_trafo_ys(y, group=group)
            else:
                return self.inv_trafo_ys(y)
        else:
            return y

    @staticmethod
    def _make_normalization(normalization: str):
        if normalization == "batchnorm":
            normalization = nn.BatchNorm1d
        else:
            normalization = nn.LayerNorm
        return normalization


if __name__ == "__main__":
    from yahpo_train.losses import *
    from yahpo_gym.configuration import cfg
    from yahpo_gym.benchmarks import iaml
    from yahpo_train.learner import SurrogateTabularLearner, dl_from_config

    device = torch.device("cpu")

    cfg = cfg("iaml_glmnet")
    dls = dl_from_config(cfg, pin_memory=True, device=device)

    model = ResNet(dls, instance_names=cfg.instance_names)
    surrogate = SurrogateTabularLearner(
        dls, model, loss_func=MultiMaeLoss(), metrics=None
    )
    surrogate.fit_one_cycle(5, 1e-4)
