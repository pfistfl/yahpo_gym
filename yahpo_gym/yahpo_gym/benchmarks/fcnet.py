from yahpo_gym.configuration import config_dict

_fcnet_dict = {
    'config_id' : 'fcnet',
    'y_names' : ['valid_loss', 'valid_mse', 'runtime', 'n_params'],
    'y_minimize' : [True, True, True, True],
    'cont_names': ['epoch', 'replication','batch_size','dropout_1','dropout_2','init_lr', 'units_1','n_units_2'],
    'cat_names': ['task', 'activation_fn_1' , 'activation_fn_2', 'lr_schedule'],
    'instance_names': 'task',
    'fidelity_params': ['epoch', 'replication'],
    'runtime_name': 'runtime',
    'citation': 'S. Falkner, A. Klein, and F. Hutter, “BOHB: Robust and Efficient Hyperparameter Optimization at Scale,” in International Conference on Machine Learning, pp. 1437–1446, PMLR, July 2018.'
}
config_dict.update({'fcnet' : _fcnet_dict})
