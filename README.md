# layout-generator

本仓库分享一个框架结构数据集的创建流程：先生成规则化 RC 框架结构 JSON，再转换为 E2K，导入 PKPM 完成结构计算，最后从 PKPM 工程文件中抽取设计结果和配筋结果。

仓库只公开方法、脚本和少量样例结果。完整批量 JSON、E2K、PKPM 工程目录、PDB/SAT/OUT/DB 等软件产物不进入 Git，避免仓库过大，也避免把中间工程文件直接公开。

## 目录说明

- `run_layout.py`：批量生成 1 到 9 号布局的结构 JSON。
- `1/` 到 `9/`：不同布局类型的生成器。
- `tools/json_to_e2k.py`：把结构 JSON 转换为 E2K。
- `tools/extract_pkpm_files.py`：从 PKPM 工程目录抽取后处理所需文件。
- `tools/extract_structure_from_t.py`：从抽取后的 PKPM 文件中生成包含设计结果和配筋结果的数据 JSON。
- `examples/`：最小链路样例，包括一个布局 JSON、对应 E2K、一个 PKPM 后处理结果 JSON。

## 运行环境

本项目在 Windows 下使用现有 Anaconda 环境测试：

```powershell
& "C:\Project\anaconda3\envs\torch_cuda\python.exe" -m py_compile ".\run_layout.py"
```

生成器依赖 Python 和 PyTorch。可视化路径在 Matplotlib 可用时启用，但批量生成默认关闭可视化。

## 1. 生成结构 JSON

在仓库根目录执行：

```powershell
& "C:\Project\anaconda3\envs\torch_cuda\python.exe" .\run_layout.py --outdir ".\out"
```

脚本会按楼层数、跨数、跨度和布局类型批量生成结构 JSON，默认输出到 `out/story_batches/`。该目录是生成产物，不纳入 Git。

## 2. JSON 转 E2K

把生成的 JSON 转换为可导入 PKPM 的 E2K：

```powershell
& "C:\Project\anaconda3\envs\torch_cuda\python.exe" .\tools\json_to_e2k.py ".\out\story_batches" --out ".\out_e2k\story_batches" --clean
```

也可以用仓库中的样例 JSON 做最小验证：

```powershell
& "C:\Project\anaconda3\envs\torch_cuda\python.exe" .\tools\json_to_e2k.py ".\examples\layout_json" --out ".\tmp_e2k" --clean
```

## 3. E2K 导入 PKPM

在 PKPM 中新建或打开工程，使用 E2K 导入功能导入上一步生成的 `.e2k` 文件，然后执行结构建模、SATWE 计算和施工图/配筋相关计算。PKPM 会在工程目录下生成后续抽取所需的模型、计算和施工图文件。

完整 PKPM 工程目录通常包含大量二进制和中间文件，本仓库不直接上传这些目录。

## 4. 抽取 PKPM 工程文件

对一个已经计算完成的 PKPM 工程目录执行：

```powershell
& "C:\Project\anaconda3\envs\torch_cuda\python.exe" .\tools\extract_pkpm_files.py "<PKPM工程目录>" --out "<抽取目录>"
```

该脚本只抽取后处理需要的固定文件：

- `SAT_PMXY.SAT`
- `SDATA.SAT`
- `WMASS.OUT`
- `施工图\Beam*.T`
- `施工图\ColumnWall*.T`

## 5. 提取设计结果和配筋结果

对上一步得到的抽取目录执行：

```powershell
& "C:\Project\anaconda3\envs\torch_cuda\python.exe" .\tools\extract_structure_from_t.py "<抽取目录>"
```

输出 JSON 会写入抽取目录同级的 `rc_out/`，内容包括结构几何、材料参数、设计结果和配筋结果。也可以批量处理一组 `*_extracted` 目录：

```powershell
& "C:\Project\anaconda3\envs\torch_cuda\python.exe" .\tools\extract_structure_from_t.py --all "<工作目录>"
```

## 样例

- `examples/layout_json/layout3_s04_x5_y4_span45.json`：生成器输出的结构 JSON 样例。
- `examples/e2k/layout3_s04_x5_y4_span45.e2k`：由样例 JSON 转换得到的 E2K。
- `examples/pkpm_results/stories_04_layout_00_rc_seed_7a0c1f82.json`：PKPM 后处理结果样例。

这些样例用于展示链路和输出格式，不代表完整数据集。
