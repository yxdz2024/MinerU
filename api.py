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

from magic_pdf.pipe.UNIPipe import UNIPipe
from magic_pdf.pipe.OCRPipe import OCRPipe
from magic_pdf.pipe.TXTPipe import TXTPipe
from magic_pdf.rw.DiskReaderWriter import DiskReaderWriter
import magic_pdf.model as model_config

model_config.__use_inside_model__ = True

def create_app(run_mode: str = None):
    app = FastAPI(
        title="MinerU API Server",
        version=VERSION,
        docs_url="/swagger",
        redoc_url="/redoc",
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

def read_md_dump(pipe,
        output_image_path,
        pdf_name,
        content_list,
        md_content) -> MagicPdfParseMainOutput: 

    '''
    # 写入模型结果
    orig_model_list = copy.deepcopy(pipe.model_list)
    model=orig_model_list
    '''
    
    '''
    # 写入中间结果
    middle=pipe.pdf_mid_data
    '''
    
    # text文本结果写入
    content_list=content_list

    '''
    # 写入结果到 .md 文件中
    md=md_content
    '''
    
    # 读取图片
    images = []
    for filename in os.listdir(output_image_path):
        file_path = os.path.join(output_image_path, filename)
        if os.path.isfile(file_path):
            with open(file_path, 'rb') as file:
                file_data = file.read()
                '''
                encoded_data = base64.b64encode(file_data).decode('utf-8')
                image_data = ImageData(name=filename, data=encoded_data)
                '''
                url = f"{MAGIC_PDF_IMG_URL}/{pdf_name}/images/{filename}"
                image_data = ImageData(name=filename, url=url)
                images.append(image_data)

    #return MagicPdfParseMainOutput(model=model, middle=middle, content_list=content_list, md=md, images=images)
    return MagicPdfParseMainOutput(content_list=content_list, images=images)            

def mount_app_routes(app: FastAPI, run_mode: str = None):
    @app.post("/magic_pdf/magic_pdf_parse_main",description="pdf解析",tags=["magic_pdf"])
    async def magic_pdf_parse_main(
        file:UploadFile=File(...),
        parse_method: str = Form('auto')):

        """
        执行从 pdf 转换到 json、md 的过程，输出 md 和 json 文件到 pdf 文件所在的目录
        :param file: .pdf 文件的路径，可以是相对路径，也可以是绝对路径
        :param parse_method: 解析方法， 共 auto、ocr、txt 三种，默认 auto，如果效果不好，可以尝试 ocr
        """
        
        result=BaseResultModel()

        try:
            if not os.path.exists("temp_files"):
                os.makedirs("temp_files")
                
            pdf_name = file.filename;
            new_pdf_name = str(uuid.uuid4());

            output_path = os.path.join("temp_files", new_pdf_name)
            output_image_path = os.path.join(output_path, 'images')

            # 获取图片的父路径，为的是以相对路径保存到 .md 和 conent_list.json 文件中
            image_path_parent = os.path.basename(output_image_path)

            pdf_bytes = await file.read()  # 读取 pdf 文件的二进制数据

            model_json = []

            # 执行解析步骤
            image_writer, md_writer = DiskReaderWriter(output_image_path), DiskReaderWriter(output_path)

            # 选择解析方式
            if parse_method == "auto":
                jso_useful_key = {"_pdf_type": "", "model_list": model_json}
                pipe = UNIPipe(pdf_bytes, jso_useful_key, image_writer)
            elif parse_method == "txt":
                pipe = TXTPipe(pdf_bytes, model_json, image_writer)
            elif parse_method == "ocr":
                pipe = OCRPipe(pdf_bytes, model_json, image_writer)
            else:
                raise Exception("unknown parse method, only auto, ocr, txt allowed")

            # 执行分类
            pipe.pipe_classify()

            # 如果没有传入模型数据，则使用内置模型解析
            if not model_json:
                if model_config.__use_inside_model__:
                    pipe.pipe_analyze()  # 解析
                else:
                    raise Exception("unknown parse method, only auto, ocr, txt allowed")

            # 执行解析
            pipe.pipe_parse()

            # 保存 text 和 md 格式的结果
            content_list = pipe.pipe_mk_uni_format(image_path_parent, drop_mode="none")
            md_content = pipe.pipe_mk_markdown(image_path_parent, drop_mode="none")

            result.data=read_md_dump(pipe, output_image_path, new_pdf_name, content_list, md_content)

            # 清除文件夹
            # shutil.rmtree(output_path)

            return result

        except Exception as e:
            logger.exception(e)
            
            result.code=500
            result.msg="处理异常"
            return result

if __name__=="__main__":
    parser = argparse.ArgumentParser(prog='MinerUSideCar',
                                     description='MinerUSidCar')
    parser.add_argument("--host", type=str, default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8090)
    parser.add_argument("--ssl_keyfile", type=str)
    parser.add_argument("--ssl_certfile", type=str)
    # 初始化消息
    args = parser.parse_args()
    args_dict = vars(args)
    app = create_app()

    uvicorn.run(app, host=args.host, port=args.port)