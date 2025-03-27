from typing import List
from fastapi import UploadFile
from fastapi.responses import JSONResponse
import os
import json
import io
import uuid

from configs.base_config import MAGIC_PDF_IMG_URL

from domain.dto.base_dto import BaseResultModel
from domain.dto.output.magic_pdf_parse_main_output import ImageData, MagicPdfParseMainOutput

import magic_pdf.model as model_config
from magic_pdf.config.enums import SupportedPdfParseMethod
from magic_pdf.data.data_reader_writer import FileBasedDataWriter
from magic_pdf.data.dataset import PymuDocDataset
from magic_pdf.model.doc_analyze_by_custom_model import doc_analyze
from magic_pdf.operators.models import InferenceResult

from loguru import logger

model_config.__use_inside_model__ = True

async def read_md_dump(pipe,
        output_image_path,
        pdf_name,
        content_list,
        md_content,
        odl_pdf_name,
        is_save_local,
        local_output_path) -> MagicPdfParseMainOutput: 

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
    if os.path.exists(output_image_path):
        for filename in os.listdir(output_image_path):
            file_path = os.path.join(output_image_path, filename)
            if os.path.isfile(file_path):
                with open(file_path, 'rb') as file:
                    file_data = file.read()
                    url = f"{MAGIC_PDF_IMG_URL}/{pdf_name}/images/{filename}"
                    image_data = ImageData(name=filename, url=url)
                    images.append(image_data)

    
    if is_save_local ==True:
        md_writer=FileBasedDataWriter(local_output_path)

        md_writer.write_string(
            f"{odl_pdf_name}_content_list.json",
            json.dumps(content_list, ensure_ascii=False, indent=4)
        )

        md_writer.write_string(
            f"{odl_pdf_name}_images.json",
            json.dumps([row.__dict__ for row in images], ensure_ascii=False, indent=4),
        )

    #return MagicPdfParseMainOutput(model=model, middle=middle, content_list=content_list, md=md, images=images)
    return MagicPdfParseMainOutput(content_list=content_list, images=images)            

async def magic_pdf_parse_main(
    file:UploadFile,
    parse_method: str="auto",
    is_save_local: bool=False,
    local_output_path: str=None,
    ) ->BaseResultModel:

    """
    执行从 pdf 转换到 json、md 的过程，输出 md 和 json 文件到 pdf 文件所在的目录
    :param file: .pdf 文件的路径，可以是相对路径，也可以是绝对路径
    :param parse_method: 解析方法， 共 auto、ocr、txt 三种，默认 auto，如果效果不好，可以尝试 ocr
    :param is_save_local: 是否把输出的内容保存在本地
    :param local_output_path: 本地保存地址
    """
    
    result=BaseResultModel()
    
    try:
        if not os.path.exists("temp_files"):
            os.makedirs("temp_files")
            
        pdf_name = file.filename

        name_without_suff = pdf_name.split(".")[0]

        new_pdf_name = str(uuid.uuid4())
    
        if not local_output_path:
            local_output_path="temp_files"

        output_path = os.path.join("temp_files", new_pdf_name)
        output_image_path = os.path.join(output_path, 'images')
    
        # 获取图片的父路径，为的是以相对路径保存到 .md 和 conent_list.json 文件中
        image_path_parent = os.path.basename(output_image_path)
    
        pdf_bytes = await file.read()  # 读取 pdf 文件的二进制数据
    
        model_json = []
    
        # 执行解析步骤
        image_writer, md_writer = FileBasedDataWriter(output_image_path), FileBasedDataWriter(local_output_path)
    
        '''
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
        '''
    
        ds = PymuDocDataset(pdf_bytes)
        # Choose parsing method
        if parse_method == 'auto':
            if ds.classify() == SupportedPdfParseMethod.OCR:
                parse_method = 'ocr'
            else:
                parse_method = 'txt'

        if parse_method not in ['txt', 'ocr']:
            logger.error('Unknown parse method, only auto, ocr, txt allowed')
            return JSONResponse(
                content={'error': 'Invalid parse method'}, status_code=400
            )

        if len(model_json) == 0:
            if parse_method == 'ocr':
                infer_result = ds.apply(doc_analyze, ocr=True)
            else:
                infer_result = ds.apply(doc_analyze, ocr=False)

        else:
            infer_result = InferenceResult(model_json, ds)

        if len(model_json) == 0 and not model_config.__use_inside_model__:
                logger.error('Need model list input')
                return JSONResponse(
                    content={'error': 'Model list input required'}, status_code=400
                )
        if parse_method == 'ocr':
            pipe_res = infer_result.pipe_ocr_mode(image_writer)
        else:
            pipe_res = infer_result.pipe_txt_mode(image_writer)
    
        # 保存 text 和 md 格式的结果
        content_list = pipe_res.get_content_list(image_path_parent, drop_mode="none")
        
        md_content = pipe_res.get_markdown(image_path_parent, drop_mode="none")

        #pipe_res.draw_layout(os.path.join(local_output_path, f"{new_pdf_name}_layout.pdf"))

        #pipe_res.dump_md(md_writer, f"{new_pdf_name}.md", str(os.path.basename(local_output_path)))
    
        result.data=await read_md_dump(pipe_res, output_image_path, new_pdf_name, content_list, md_content, file.filename, is_save_local, local_output_path)
    
        # 清除文件夹
        # shutil.rmtree(output_path)
    
        return result
    
    except Exception as e:
        logger.exception(e)
        
        result.code=500
        result.msg="处理异常"
        return result

async def magic_pdf_parse_main2(file:UploadFile,
    parse_method: str="auto",
    is_save_local: bool=True,
    local_output_path: str=None,
    ) ->BaseResultModel:

    """
    执行从 pdf 转换到 json、md 的过程，输出 md 和 json 文件到 pdf 文件所在的目录
    :param file: .pdf 文件的路径，可以是相对路径，也可以是绝对路径
    :param parse_method: 解析方法， 共 auto、ocr、txt 三种，默认 auto，如果效果不好，可以尝试 ocr
    :param is_save_local: 是否把输出的内容保存在本地
    :param local_output_path: 本地保存地址
    """

    result=BaseResultModel()
    try:
        if not local_output_path:
            local_output_path = 'temp_files' # 读取文件夹中的所有文件


        pdf_name = file.filename

        name_without_suff = pdf_name.split(".")[0]


        output_path = os.path.join(local_output_path, name_without_suff)
        
        output_image_path = os.path.join(output_path, 'images')

        image_dir = str(os.path.basename(output_image_path))
            # 获取图片的父路径，为的是以相对路径保存到 .md 和 conent_list.json 文件中
        image_path_parent = os.path.basename(output_image_path)

        pdf_bytes = await file.read()  # 读取 pdf 文件的二进制数据

        model_json = []

        # 执行解析步骤
        image_writer, md_writer = FileBasedDataWriter(output_image_path), FileBasedDataWriter(local_output_path)

        '''
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
        '''

        ds = PymuDocDataset(pdf_bytes)
        # Choose parsing method
        if parse_method == 'auto':
            if ds.classify() == SupportedPdfParseMethod.OCR:
                parse_method = 'ocr'
            else:
                parse_method = 'txt'

        if parse_method not in ['txt', 'ocr']:
            logger.error('Unknown parse method, only auto, ocr, txt allowed')
            return JSONResponse(
                content={'error': 'Invalid parse method'}, status_code=400
            )

        if parse_method == 'ocr':
            infer_result = ds.apply(doc_analyze, ocr=True)        
            pipe_res = infer_result.pipe_ocr_mode(image_writer)

        else:
            infer_result = ds.apply(doc_analyze, ocr=False)
            pipe_res = infer_result.pipe_txt_mode(image_writer)


        # 保存 text 和 md 格式的结果

        ### draw layout result on each page
        pipe_res.draw_layout(os.path.join(output_path, f"{name_without_suff}_layout.pdf"))

        pipe_res.dump_md(md_writer, os.path.join(name_without_suff,f"{name_without_suff}.md"), name_without_suff)

        ### dump content list
        pipe_res.dump_content_list(md_writer, os.path.join(name_without_suff,f"{name_without_suff}_content_list.json"), name_without_suff)

        return result
    
    except Exception as e:
        logger.exception(e)
        
        result.code=500
        result.msg="处理异常"
        return result

    


async def magic_pdf_parse_main_batch(
    folder_path:str="",
    parse_method: str="auto"
    ) ->BaseResultModel:
    result=BaseResultModel()

    if not os.path.exists("batch_files"):
        os.makedirs("batch_files")

    print(f"批处理:正在加载本地文件")

    if not folder_path:
        folder_path = 'batch_files' # 读取文件夹中的所有文件 

        # 获取当前目录下已处理文件集合，存储为 {name_without_suff: path}
    processed_files = set()
    for root, _, files in os.walk(folder_path):
        for file_name in files:
            if file_name.endswith("_layout.pdf"):
                processed_files.add(file_name.replace("_layout.pdf", ""))


    upload_files: List[UploadFile] = []
    # 遍历PDF文件并过滤未处理的文件
    for root, _, files in os.walk(folder_path):
        for file_name in files:
            # 文件名去除_layout.pdf或其他干扰，避免误处理文件
            if file_name.endswith(".pdf") and "_layout" not in file_name:
                name_without_suff = file_name.replace(".pdf", "")
                if name_without_suff not in processed_files:
                    file_path = os.path.join(root, file_name)
                    with open(file_path, "rb") as file:
                        file_data = file.read()
                        upload_file = UploadFile(file=io.BytesIO(file_data), filename=file_name)
                        upload_files.append(upload_file)
                else:
                    print(f"跳过:{file_name}")

    print(f"批处理:完成加载本地文件")

    print(f"批处理:正在处理本地文件")

    error_file=[]
    total=len(upload_files)
    index=1
    for upload_file in upload_files:
        print(f"批处理:正在处理文件 {upload_file.filename} {index}/{total}")
        magic_pdf_parse_main_result = await magic_pdf_parse_main2(upload_file, parse_method, True, folder_path)
        if magic_pdf_parse_main_result.code != 200:
            error_file.append(upload_file.filename)
            print(f"批处理:异常处理文件 {upload_file.filename} {index}/{total}")
        else:
            print(f"批处理:完成处理文件 {upload_file.filename} {index}/{total}")

        index=index+1

    compelete_str = "批处理:完成处理本地文件。"
    if len(error_file) > 0:
        compelete_str = compelete_str + f"失败文件有 {','.join(error_file)}"
    print(compelete_str)

    return result