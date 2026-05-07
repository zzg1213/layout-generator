# layout-generator

Deterministic RC frame layout generator for producing JSON layout batches from
nine layout generator variants.

## Project Layout

- `run_layout.py` runs every supported layout configuration and writes JSON
  outputs under `out/story_batches/`.
- `1/` through `9/` contain the individual layout generator implementations.
- Generated output directories such as `out/` and `out_e2k/` are intentionally
  not tracked in Git because they can be regenerated and are large.

## Environment

Use the existing Anaconda environment:

```powershell
& "C:\Project\anaconda3\envs\torch_cuda\python.exe" -m py_compile "run_layout.py"
```

The generator modules depend on Python and PyTorch. Some optional visualization
paths use Matplotlib when it is available.

## Generate Layouts

From the project root:

```powershell
& "C:\Project\anaconda3\envs\torch_cuda\python.exe" .\run_layout.py
```

To write outputs to a custom directory:

```powershell
& "C:\Project\anaconda3\envs\torch_cuda\python.exe" .\run_layout.py --outdir ".\out"
```

The full generation run may create many JSON files and can take significant
time depending on hardware and CUDA availability.
