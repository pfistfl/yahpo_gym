# YAHPO GYM (R Package)

R Interface for the YAHPO GYM python module.


## Installation

The package can be installed from GitHub via


```r
remotes::install_github("pfistfl/yahpo_gym/yahpo_gym_r").
```

### Setup

YAHPO GYM requires a one-time setup to install the required python dependencies.
Here we install all packages into the `yahpo_gym` conda environment.

```r
reticulate::conda_create(
  envname = "yahpo_gym",
  packages = c("onnxruntime", "pip", "pyyaml", "pandas"),
  channel = "conda-forge",
  python_version = "3.8"
)
reticulate::conda_install(envname = "yahpo_gym", packages="configspace", channel="conda-forge")
reticulate::conda_install(envname = "yahpo_gym", packages="fastdownload", channel="fastai")
reticulate::conda_install(envname = "yahpo_gym", pip=TRUE,
  packages="'git+https://github.com/pfistfl/yahpo_gym#egg=yahpo_gym&subdirectory=yahpo_gym'")
```

Now we can instantiate a local config that sets up the path files are downloaded to:

```r
reticulate::use_condaenv("yahpo_gym", required=TRUE)
library("yahpogym")
init_local_config(data_path = "~/multifidelity_data")
```


## Usage

We first load the package and the required conda environment:

```r
reticulate::use_condaenv("yahpo_gym", required=TRUE)
library("yahpogym")
```

and subsequently instantiate the benchmark (random search, full fidelity) to obtain our objective.

```r
b = BenchmarkSet$new("iaml_glmnet", download = FALSE)
obj = b$get_objective("40981", multifidelity = FALSE)
```

and run our search procedure.

```r
library("bbotk")
p = opt("random_search")
ois = OptimInstanceMultiCrit$new(obj, search_space = b$get_search_space(drop_fidelity_params = TRUE), terminator = trm("evals", n_evals = 10))
p$optimize(ois)
```



### or with Hyperband using (`mlr3hyperband`)

```r
library(mlr3hyperband)
obj = b$get_objective("40981", multifidelity = TRUE)
ois = OptimInstanceMultiCrit$new(obj, search_space = b$get_search_space(), terminator = trm("none"))
p = opt("hyperband")
p$optimize(ois)
```


### Available Problems

We can list all available benchmark problems

```r
list_benchmarks()
```

and available instances in a `Benchmark`:

```r
b$instances
```

## Using yahpogym with `future`:

Parallelization with `future` and `reticulate` does not always work out of the box.
The following configurations allow to use `yahpogym` together with `future`.

1. If `yahpogym` requires a conda env / virtual env set up the `.Renvirion` file by adding 
  `RETICULATE_PYTHON=path_to_conda_python_bin`. This path can be obtained through `reticulate::py_discover_config()`.

2. Silence `future` warnings using `options(future.globals.onReference = "string")`.
  Note: `future`s check will still find unresolved references, but `yahpogym` constructs those on the child process via active bindings.

3. Run the evaluation using `future`:
  ```r
    b = BenchmarkSet$new("lcbench")
    objective = b$get_objective("3945", check_values = FALSE)

    xdt = generate_design_random(b$get_search_space(), 1)$data
    xss_trafoed = transform_xdt_to_xss(xdt, b$get_search_space())

    future::plan("multisession")
    promise = future::future(objective$eval_many(xss_trafoed), packages = "yahpogym", seed = NULL)
    future::value(promise)
  ```