from datetime import datetime
import os
import sys

'''
服务相关
'''
ALLOW_ORIGINS=["*"]

VERSION="0.0.1"

'''
默认解析临时文件地址
'''
MAGIC_PDF_IMG_URL="http://192.168.88.244:8092"

'''
VLLM相关默认配置
'''
VLLM_CONFIG = {
    "mem_fraction_static": 0.6
}