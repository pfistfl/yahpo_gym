from yahpo_gym.configuration import config_dict

_lcbench_dict = {
    "config_id": "lcbench",
    "y_names": [
        "val_accuracy",
        "val_cross_entropy",
        "val_balanced_accuracy",
        "test_accuracy",
        "test_cross_entropy",
        "test_balanced_accuracy",
        "time",
        "time_increase",
        # "model_parameters",
    ],
    # "y_minimize": [False, True, False, False, True, False, True, True, True],
    "y_minimize": [False, True, False, False, True, False, True, True],
    "cont_names": [
        "epoch",
        "batch_size",
        "learning_rate",
        "momentum",
        "weight_decay",
        "num_layers",
        "max_units",
        "max_dropout",
    ],
    "cat_names": ["OpenML_task_id"],
    "instance_names": "OpenML_task_id",
    "fidelity_params": ["epoch"],
    "runtime_name": "time",
    "citation": [
        "Zimmer, L., Lindauer, M., & Hutter, F. (2021). Auto-Pytorch: Multi-Fidelity Metalearning for Efficient and Robust AutoDL. IEEE Transactions on Pattern Analysis and Machine Intelligence, 43(9), 3079-3090.",
        "Zimmer, L. (2020). data_2k_lw.zip. figshare. Dataset. https://doi.org/10.6084/m9.figshare.11662422.v1, Apache License, Version 2.0.",
    ],
}
config_dict.update({"lcbench": _lcbench_dict})
