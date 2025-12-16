# 基底包环境说明
## 参数
CUDA版本: 12.8
Python版本：3.10

## 默认pip安装
pip install torch==2.7.0+cu128 torchvision==0.22.0+cu128 torchaudio==2.7.0+cu128 -i https://download.pytorch.org/whl/cu128
pip install packaging==25.0
pip install flash-attn==2.8.0.post2
pip install ninja==3.3.20
pip install nvidia-nvshmem-cu12==3.3.20
pip install nvidia-cudnn-frontend==1.13.0
pip install pynvml==12.0.0
pip install requests==2.32.4
pip install cuda-python==13.0.0
pip install cuda-bindings==13.0.0

## 编译 flashinfer-python-0.2.9rc2
/home/flashinfer-python-0.2.9rc2
python -m pip install --no-build-isolation -v .

UNKNOWN 就是 flashinfer-python-0.2.9rc2

Package                  Version
------------------------ ------------
certifi                  2025.11.12
charset-normalizer       3.4.4
cuda-bindings            13.0.0
cuda-pathfinder          1.3.3
cuda-python              13.0.0
einops                   0.8.1
filelock                 3.20.0
flash-attn               2.8.0.post2
fsspec                   2025.12.0
idna                     3.11
Jinja2                   3.1.6
MarkupSafe               2.1.5
mpmath                   1.3.0
networkx                 3.4.2
ninja                    1.13.0
numpy                    2.2.6
nvidia-cublas-cu12       12.8.3.14
nvidia-cuda-cupti-cu12   12.8.57
nvidia-cuda-nvrtc-cu12   12.8.61
nvidia-cuda-runtime-cu12 12.8.57
nvidia-cudnn-cu12        9.7.1.26
nvidia-cudnn-frontend    1.13.0
nvidia-cufft-cu12        11.3.3.41
nvidia-cufile-cu12       1.13.0.11
nvidia-curand-cu12       10.3.9.55
nvidia-cusolver-cu12     11.7.2.55
nvidia-cusparse-cu12     12.5.7.53
nvidia-cusparselt-cu12   0.6.3
nvidia-cutlass-dsl       4.3.3
nvidia-ml-py             12.575.51
nvidia-nccl-cu12         2.26.2
nvidia-nvjitlink-cu12    12.8.61
nvidia-nvshmem-cu12      3.3.20
nvidia-nvtx-cu12         12.8.55
packaging                25.0
pillow                   12.0.0
pip                      22.0.2
pynvml                   12.0.0
requests                 2.32.4
setuptools               59.6.0
sympy                    1.14.0
tabulate                 0.9.0
torch                    2.7.0+cu128
torchaudio               2.7.0+cu128
torchvision              0.22.0+cu128
triton                   3.3.0
typing_extensions        4.15.0
UNKNOWN                  0.2.9rc2
urllib3                  2.6.2
wheel                    0.37.1