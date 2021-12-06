from yahpo_train.model import *
from yahpo_train.metrics import *
from yahpo_train.cont_scalers import *
from yahpo_gym.benchmarks import lcbench, rbv2, nasbench_301, fcnet, taskset, iaml
from yahpo_gym.configuration import cfg
from fastai.callback.wandb import *
from functools import partial
import wandb

def fit_config(key, embds_dbl=None, embds_tgt=None, tfms=None, lr=1e-4, epochs=100, deep=[1024,512,256], deeper=[], dropout=0., wide=True, use_bn=False, frac=1., bs=10240, mixup=True, export=False, log_wandb=True, wandb_entity='mfsurrogates', cbs=[], device='cuda:0'):
    """
    Fit function with hyperparameters
    """
    cc = cfg(key)
    dls = dl_from_config(cc, bs=bs, frac=frac)

    # Construct embds from transforms. tfms overwrites emdbs_dbl, embds_tgt
    if tfms is not None:
        embds_dbl = [tfms.get(name) if tfms.get(name) is not None else ContTransformerNone for name, cont in dls.all_cols[dls.cont_names].iteritems()]
        embds_tgt = [tfms.get(name) if tfms.get(name) is not None else ContTransformerNone for name, cont in dls.ys.iteritems()]

    # Instantiate learner
    f = FFSurrogateModel(dls, layers=deep, deeper=deeper, ps=dropout, use_bn=use_bn, wide=wide, embds_dbl=embds_dbl, embds_tgt=embds_tgt)
    l = SurrogateTabularLearner(dls, f, loss_func=nn.MSELoss(reduction='mean'), metrics=nn.MSELoss)
    l.metrics = [AvgTfedMetric(mae), AvgTfedMetric(r2), AvgTfedMetric(spearman), AvgTfedMetric(napct)]
    if mixup:
        l.add_cb(MixHandler)
    l.add_cb(EarlyStoppingCallback(patience=10))
    if len(cbs):
        [l.add_cb(cb) for cb in cbs]

    # Log results to wandb
    if log_wandb:
        wandb.init(project=key, entity=wandb_entity)
        l.add_cb(WandbMetricsTableCallback())
        wandb.config.update({'cont_tf': l.embds_dbl, 'tgt_tf': l.embds_tgt, 'fraction': frac,}, allow_val_change=True)
        wandb.config.update({'deep': deep, 'deeper': deeper, 'dropout':dropout, 'wide':wide, 'use_bn':use_bn}, allow_val_change=True)

    # Fit
    l.fit_flat_cos(epochs, lr)

    if log_wandb: 
        wandb.finish()

    if export:
        l.export_onnx(cc, device=device)
        
    return l


def get_arch(max_units, n, shape):
    if max_units == 0:
        return []
    if n == 0:
       n = 4
    if shape == "square":
        return [2**max_units for x in range(n)]
    if shape == "cone":
        units = [2**max_units]
        for x in range(n):
            units += [int(units[-1]/2)]
        return units


def tune_config(key, name, **kwargs):
    import optuna
    from optuna.integration import FastAIPruningCallback
    from optuna.visualization import plot_optimization_history
    import logging
    import sys

    optuna.logging.get_logger("optuna").addHandler(logging.StreamHandler(sys.stdout))
    
    storage_name = "sqlite:///{}.db".format(name)
    pruner = optuna.pruners.MedianPruner(n_startup_trials=5, n_warmup_steps=10)
    study = optuna.create_study(study_name=name, storage=storage_name, direction="minimize", pruner=pruner, load_if_exists=True)

    trange = ContTransformerRange
    tlog = ContTransformerLogRange
    tnexp = ContTransformerNegExpRange
    trafos = {"trange":trange, "tlog":tlog, "tnexp":tnexp}
    
    def objective(trial):
        cc = cfg(key)
        tfms = {}
        # FIXME: we probably want to be able to manually exclude ys and xs and simply provide tfs
        for y in cc.y_names:
            # if opt_tfms_y is False use ContTransformerRange
            opt_tfms_y = trial.suggest_categorical("opt_tfms_" + y, [True, False])
            if opt_tfms_y:
                tf = trial.suggest_categorical("tfms_" + y, ["tlog", "tnexp"])
            else:
                tf = "trange"
            tfms.update({y:trafos.get(tf)})
        for x in cc.cont_names:
            # if opt_tfms_x is False use ContTransformerRange
            opt_tfms_x = trial.suggest_categorical("opt_tfms_" + x, [True, False])
            if opt_tfms_x:
                tf = trial.suggest_categorical("tfms_" + x, ["tlog", "tnexp"])
            else:
                tf = "trange"
            tfms.update({x:trafos.get(tf)})

        opt_deep_arch = trial.suggest_categorical("opt_deep_arch", [True, False])
        if opt_deep_arch:
            deep_u = trial.suggest_categorical("deep_u", [7, 8, 9, 10])
            deep_n = trial.suggest_categorical("deep_n", [0, 1, 2, 3])
            deep_s = trial.suggest_categorical("deep_s", ["square", "cone"])
            deep = get_arch(deep_u, deep_n, deep_s)
            use_deeper = trial.suggest_categorical("use_deeper", [True, False])
            if use_deeper:
                deeper_u = trial.suggest_categorical("deeper_u", [7, 8, 9, 10])
                deeper = get_arch(deeper_u, deep_n + 2, deep_s)
            else:
                deeper = []
        else:
            deep = [1024,512,256]
            deeper = []

        lr = trial.suggest_float("lr", 1e-5, 1e-3, log=True)
        wide = trial.suggest_categorical("wide", [True, False])
        mixup = trial.suggest_categorical("mixup", [True, False])
        use_bn = trial.suggest_categorical("use_bn", [True, False])
        dropout = trial.suggest_categorical("dropout", [0., 0.25, 0.5])
        cbs = [FastAIPruningCallback(trial=trial, monitor='valid_loss')]
        
        l = fit_config(key=key, tfms=tfms, lr=lr, deep=deep, deeper=deeper, wide=wide, mixup=mixup, use_bn=use_bn, dropout=dropout, log_wandb=False, cbs=cbs, **kwargs)
        return l.recorder.losses[-1]
    
    study.optimize(objective, n_trials=100, timeout=86400)
    # plot_optimization_history(study)
    return study

def fit_from_best_params(key, best_params, log_wandb=False, **kwargs):
    cc = cfg(key)
    tfms = {}

    trange = ContTransformerRange
    tlog = ContTransformerLogRange
    tnexp = ContTransformerNegExpRange
    trafos = {"trange":trange, "tlog":tlog, "tnexp":tnexp}

    for y in cc.y_names:
        # if opt_tfms_y is False use ContTransformerRange
        opt_tfms_y = best_params.get("opt_tfms_" + y)
        if opt_tfms_y:
            tf = best_params.get("tfms_" + y)
        else:
            tf = "trange"
        tfms.update({y:trafos.get(tf)})
    for x in cc.cont_names:
        # if opt_tfms_x is False use ContTransformerRange
        opt_tfms_x = best_params.get("opt_tfms_" + x)
        if opt_tfms_x:
            tf = best_params.get("tfms_" + x)
        else:
            tf = "trange"
        tfms.update({x:trafos.get(tf)})

    if best_params.get("opt_deep_arch"):
        deep = get_arch(best_params.get("deep_u"), best_params.get("deep_n"), best_params.get("deep_s"))
        use_deeper = best_params.get("use_deeper")
        if use_deeper:
            deeper = get_arch(best_params.get("deeper_u"), best_params.get("deep_n") + 2, best_params.get("deep_s"))
        else:
            deeper = []
    else:
        deep = [1024,512,256]
        deeper = []

    lr = best_params.get("lr")
    wide = best_params.get("wide")
    mixup = best_params.get("mixup")
    use_bn = best_params.get("use_bn")
    dropout = best_params.get("dropout")
    
    l = fit_config(key=key, tfms=tfms, lr=lr, deep=deep, deeper=deeper, wide=wide, mixup=mixup, use_bn=use_bn, dropout=dropout, log_wandb=log_wandb, **kwargs)
    return l


def fit_nb301(key='nb301', **kwargs):
    embds_dbl = [partial(ContTransformerMultScalar, m=1/52)]
    embds_tgt = [partial(ContTransformerMultScalar, m=1/100), ContTransformerRange]
    return fit_config(key, embds_dbl=embds_dbl, embds_tgt=embds_tgt, **kwargs)


def fit_rbv2_super(key='rbv2_super', **kwargs):
    # Transforms
    tfms = {}
    [tfms.update({k:ContTransformerRange}) for k in ["mmce", "f1", "auc", "aknn.k", "aknn.M", "rpart.maxdepth", "rpart.minsplit", "rpart.minbucket", "xgboost.max_depth"]]
    [tfms.update({k:partial(ContTransformerLogRange)}) for k in ["timetrain", "timepredict", "svm.cost", "svm.gamma"]]
    [tfms.update({k:partial(ContTransformerLogRange, logfun=torch.log2, expfun=torch.exp2)}) for k in ["glmnet.s", "rpart.cp", "aknn.ef", "aknn.ef_construction", "xgboost.nrounds", "xgboost.eta", "xgboost.gamma", "xgboost.lambda", "xgboost.alpha", "xgboost.min_child_weight", "ranger.num.trees", "ranger.min.node.size", 'ranger.num.random.splits']]
    [tfms.update({k:ContTransformerNegExpRange}) for k in ["logloss"]]
    return fit_config(key, tfms=tfms, **kwargs)


def fit_rbv2_svm(key='rbv2_svm', **kwargs):
    # Transforms
    tfms = {}
    [tfms.update({k:ContTransformerRange}) for k in ["mmce", "f1", "auc"]]
    [tfms.update({k:partial(ContTransformerLogRange, logfun=torch.log, expfun=torch.exp)}) for k in ["timetrain", "timepredict", "cost", "gamma"]]
    [tfms.update({k:ContTransformerNegExpRange}) for k in ["logloss"]]
    return fit_config(key, tfms=tfms, **kwargs)


def fit_rbv2_xgboost(key='rbv2_xgboost', **kwargs):
    # Transforms
    tfms = {}
    [tfms.update({k:ContTransformerRange}) for k in ["mmce", "f1", "auc", "max_depth"]]
    [tfms.update({k:partial(ContTransformerLogRange, logfun=torch.log, expfun=torch.exp)}) for k in ["timetrain", "timepredict"]]
    [tfms.update({k:partial(ContTransformerLogRange, logfun=torch.log2, expfun=torch.exp2)}) for k in ["nrounds", "eta", "gamma", "lambda", "alpha", "min_child_weight"]]
    [tfms.update({k:ContTransformerNegExpRange}) for k in ["logloss"]]
    return fit_config(key, tfms=tfms, **kwargs)


def fit_rbv2_ranger(key='rbv2_ranger', **kwargs):
    # Transforms
    tfms = {}
    [tfms.update({k:ContTransformerRange}) for k in ["mmce", "f1", "auc"]]
    [tfms.update({k:partial(ContTransformerLogRange)}) for k in ["timetrain", "timepredict"]]
    [tfms.update({k:partial(ContTransformerLogRange, logfun=torch.log2, expfun=torch.exp2)}) for k in ["num.trees", "min.node.size", 'num.random.splits']]
    [tfms.update({k:ContTransformerNegExpRange}) for k in ["logloss"]]
    return fit_config(key, tfms=tfms, **kwargs)


def fit_rbv2_rpart(key='rbv2_rpart', **kwargs):
    # Transforms
    tfms = {}
    [tfms.update({k:ContTransformerRange}) for k in ["mmce", "f1", "auc", "maxdepth", "minsplit", "minbucket"]]
    [tfms.update({k:partial(ContTransformerLogRange)}) for k in ["timetrain", "timepredict"]]
    [tfms.update({k:partial(ContTransformerLogRange, logfun=torch.log2, expfun=torch.exp2)}) for k in ["cp"]]
    [tfms.update({k:ContTransformerNegExpRange}) for k in ["logloss"]]
    return fit_config(key, tfms=tfms, **kwargs)


def fit_rbv2_glmnet(key='rbv2_glmnet', **kwargs):
    # Transforms
    tfms = {}
    [tfms.update({k:ContTransformerRange}) for k in ["mmce", "f1", "auc"]]
    [tfms.update({k:partial(ContTransformerLogRange)}) for k in ["timetrain", "timepredict",]]
    [tfms.update({k:partial(ContTransformerLogRange, logfun=torch.log2, expfun=torch.exp2)}) for k in ["s"]]
    [tfms.update({k:ContTransformerNegExpRange}) for k in ["logloss"]]
    return fit_config(key, tfms=tfms, **kwargs)


def fit_rbv2_aknn(key='rbv2_aknn', **kwargs):
    # Transforms
    tfms = {}
    [tfms.update({k:ContTransformerRange}) for k in ["mmce", "f1", "auc", "k", "M"]]
    [tfms.update({k:partial(ContTransformerLogRange)}) for k in ["timetrain", "timepredict"]]
    [tfms.update({k:partial(ContTransformerLogRange, logfun=torch.log2, expfun=torch.exp2)}) for k in ["ef", "ef_construction"]]
    [tfms.update({k:ContTransformerNegExpRange}) for k in ["logloss"]]
    return fit_config(key, tfms=tfms, **kwargs)


def fit_fcnet(key='fcnet', **kwargs):
    tfms = {}
    [tfms.update({k:ContTransformerRange}) for k in ["batch_size", "n_units_1", "n_units_2"]]
    [tfms.update({k:partial(ContTransformerLogRange, logfun=torch.log, expfun=torch.exp)}) for k in ["init_lr", "runtime", "n_params"]]
    [tfms.update({k:partial(ContTransformerNegExpRange, q=.975)}) for k in ["valid_loss"]]
    return fit_config(key, tfms=tfms, **kwargs)


def fit_lcbench(key='lcbench', **kwargs):
    # Transforms
    tfms = {}
    [tfms.update({k:ContTransformerRange}) for k in ["val_accuracy", "val_balanced_accuracy", "test_balanced_accuracy", "batch_size", "max_units"]]
    [tfms.update({k:partial(ContTransformerNegExpRange, q=1.)}) for k in ["val_cross_entropy", "test_cross_entropy", "time"]]
    return fit_config(key, tfms=tfms, **kwargs)


def fit_taskset(key='taskset', **kwargs):
    # Transforms
    tfms = {}
    [tfms.update({k:ContTransformerRange}) for k in ['replication']]
    [tfms.update({k:partial(ContTransformerLogRange, logfun=torch.log2, expfun=torch.exp2)}) for k in ["epoch"]]
    [tfms.update({k:partial(ContTransformerLogRange, logfun=torch.log, expfun=torch.exp)}) for k in ["learning_rate", 'beta1', 'beta2', 'epsilon', 'l1', 'l2', 'linear_decay', 'exponential_decay']]
    [tfms.update({k:partial(ContTransformerNegExpRange, q=.99)}) for k in ["train", "valid1", "valid2", "test"]]
    return fit_config(key, tfms=tfms, **kwargs)


def fit_iaml_ranger(key='iaml_ranger', **kwargs):
    # Transforms
    tfms = {}
    [tfms.update({k:ContTransformerRange}) for k in ["mmce", "f1", "auc", "mec", "nf"]]
    [tfms.update({k:partial(ContTransformerLogRange)}) for k in ["timetrain", "timepredict", "ramtrain", "rammodel", "rampredict", "ias"]]
    [tfms.update({k:partial(ContTransformerLogRange, logfun=torch.log2, expfun=torch.exp2)}) for k in ["num.trees", "min.node.size", 'num.random.splits']]
    [tfms.update({k:partial(ContTransformerNegExpRange, q=.975)}) for k in ["logloss"]]
    return fit_config(key, tfms=tfms, **kwargs)


def fit_iaml_rpart(key='iaml_rpart', **kwargs):
    # Transforms
    tfms = {}
    [tfms.update({k:ContTransformerRange}) for k in ["mmce", "f1", "auc", "mec", "ias", "nf", "maxdepth", "minsplit", "minbucket"]]
    [tfms.update({k:partial(ContTransformerLogRange)}) for k in ["timetrain", "timepredict", "ramtrain", "rammodel", "rampredict"]]
    [tfms.update({k:partial(ContTransformerLogRange, logfun=torch.log2, expfun=torch.exp2)}) for k in ["cp"]]
    [tfms.update({k:ContTransformerNegExpRange}) for k in ["logloss"]]
    return fit_config(key, tfms=tfms, **kwargs)


def fit_iaml_glmnet(key='iaml_glmnet', **kwargs):
    # Transforms
    tfms = {}
    [tfms.update({k:ContTransformerRange}) for k in ["auc", "ias", "mec", "mmce", "nf", "rammodel", "ramtrain", "timepredict"]]
    [tfms.update({k:partial(ContTransformerLogRange)}) for k in ["alpha", "rampredict", "timetrain", "logloss", "s", "trainsize"]]
    [tfms.update({k:partial(ContTransformerNegExpRange)}) for k in ["f1"]]
    return fit_config(key, tfms=tfms, **kwargs)


def fit_iaml_xgboost(key='iaml_xgboost', **kwargs):
    # Transforms
    tfms = {}
    [tfms.update({k:ContTransformerRange}) for k in ["mmce", "f1", "auc", "mec", "ias", "nf", "max_depth"]]
    [tfms.update({k:partial(ContTransformerLogRange)}) for k in ["timetrain", "timepredict", "ramtrain", "rammodel", "rampredict"]]
    [tfms.update({k:partial(ContTransformerLogRange, logfun=torch.log2, expfun=torch.exp2)}) for k in ["nrounds", "eta", "gamma", "lambda", "alpha", "min_child_weight"]]
    [tfms.update({k:ContTransformerNegExpRange}) for k in ["logloss"]]
    return fit_config(key, tfms=tfms, **kwargs)

def fit_iaml_super(key='iaml_super', **kwargs):
    # Transforms
    tfms = {}
    [tfms.update({k:ContTransformerRange}) for k in ["mmce", "f1", "auc", "mec", "nf", "rpart.maxdepth", "rpart.minsplit", "rpart.minbucket", "xgboost.max_depth"]]
    [tfms.update({k:partial(ContTransformerLogRange)}) for k in ["timetrain", "timepredict", "ramtrain", "rammodel", "rampredict", "ias"]]
    [tfms.update({k:partial(ContTransformerLogRange, logfun=torch.log2, expfun=torch.exp2)}) for k in ["ranger.num.trees", "ranger.min.node.size", 'ranger.num.random.splits', "rpart.cp", "glmnet.s", "xgboost.nrounds", "xgboost.eta", "xgboost.gamma", "xgboost.lambda", "xgboost.alpha", "xgboost.min_child_weight"]]
    [tfms.update({k:ContTransformerNegExpRange}) for k in ["logloss"]]
    return fit_config(key, tfms=tfms, **kwargs)


if __name__ == '__main__':
    wandb.login()
    # fit_nb301(dropout=.0) # Done
    # fit_rbv2_rpart()
    # fit_rbv2_super()
    # fit_rbv2_svm()
    # fit_rbv2_xgboost()
    # fit_lcbench()
    # fit_rbv2_ranger()
    # fit_rbv2_glmnet()
    # fit_rbv2_aknn()
    # fit_fcnet()
    # fit_taskset()
    #
    # study_iaml_glmnet = tune_config("iaml_glmnet", "tune_iaml_glmnet")
    # fit_from_best_params("iaml_glmnet", study_iaml_glmnet.best_params)
    # study_iaml_rpart = tune_config("iaml_rpart", "tune_iaml_rpart")
    # fit_from_best_params("iaml_rpart", study_iaml_rpart.best_params)
    device = torch.device("cpu")
    fit_iaml_ranger(epochs=10,device=device, export=True, log_wandb=False)
    fit_iaml_rpart(epochs=10,device=device, export=True, log_wandb=False)
    fit_iaml_glmnet(epochs=10,device=device, export=True, log_wandb=False)
    fit_iaml_xgboost(epochs=10,device=device, export=True, log_wandb=False)
    fit_iaml_super(epochs=10,device=device, export=True, log_wandb=False)

