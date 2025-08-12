import argparse
import logging
import os
import shutil
import sys
import json
import copy
from configs.base_config import ALLOW_ORIGINS, VERSION, MAGIC_PDF_IMG_URL

from domain.dto.base_dto import BaseResultModel
from domain.dto.output.magic_pdf_parse_main_output import ImageData, MagicPdfParseMainOutput

from services import pdf_service

sys.path.append(os.path.dirname(os.path.dirname(__file__)))
import time
from typing import Union
import uuid
import base64
import uvicorn
from fastapi import  Body, FastAPI, File, Form, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from loguru import logger

def create_app(run_mode: str = None):
    app = FastAPI(
        title="API Server",
        version=VERSION,
        #docs_url=None,
        #redoc_url=None,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=ALLOW_ORIGINS,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    mount_app_routes(app, run_mode=run_mode)
    
    app.mount("/temp_files", StaticFiles(directory="temp_files"), name="temp_files")

    return app

def mount_app_routes(app: FastAPI, run_mode: str = None):
    # pdf解析
    @app.post("/magic_pdf/magic_pdf_parse_main",description="pdf解析",tags=["magic_pdf"])
    async def magic_pdf_parse_main(
        file:UploadFile=File(...),
        parse_method: str = Form('auto')):

        """
        执行从 pdf 转换到 json、md 的过程，输出 md 和 json 文件到 pdf 文件所在的目录
        :param file: .pdf 文件的路径，可以是相对路径，也可以是绝对路径
        :param parse_method: 解析方法， 共 auto、ocr、txt 三种，默认 auto，如果效果不好，可以尝试 ocr
        """
        
        return await pdf_service.magic_pdf_parse_main(file, parse_method)

    # pdf解析(批处理)
    @app.post("/magic_pdf/magic_pdf_parse_main_batch",description="pdf解析(批处理batch_files下的文件)",tags=["magic_pdf"])
    async def magic_pdf_parse_main_batch(
        folder_path:str="",
        parse_method: str = Form('ocr')
        ):
        
        return await pdf_service.magic_pdf_parse_main_batch(parse_method=parse_method,folder_path=folder_path)

    @app.post("/magic_pdf/upload",description="文件上传",tags=["magic_pdf"])
    async def upload(
            folder_path:str="file_upload",
            file:UploadFile=File(...),
        ):
        
        return await pdf_service.magic_pdf_parse_main2(file=file,local_output_path=folder_path)


if __name__=="__main__":
    parser = argparse.ArgumentParser(prog='MinerUSideCar',
                                     description='MinerUSidCar')
    parser.add_argument("--host", type=str, default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8090)
    parser.add_argument("--ssl_keyfile", type=str)
    parser.add_argument("--ssl_certfile", type=str)

    os.environ["MINERU_MODEL_SOURCE"] = "local"

    # 初始化消息
    args = parser.parse_args()
    args_dict = vars(args)
    app = create_app()

    uvicorn.run(app, host=args.host, port=args.port)