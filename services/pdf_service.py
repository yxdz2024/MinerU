from fastapi import UploadFile
import os
import json
import io
import uuid

from configs.base_config import MAGIC_PDF_IMG_URL

from domain.dto.base_dto import BaseResultModel
from domain.dto.output.magic_pdf_parse_main_output import ImageData, MagicPdfParseMainOutput

from magic_pdf.pipe.UNIPipe import UNIPipe
from magic_pdf.pipe.OCRPipe import OCRPipe
from magic_pdf.pipe.TXTPipe import TXTPipe
from magic_pdf.rw.DiskReaderWriter import DiskReaderWriter
import magic_pdf.model as model_config

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
        md_writer=DiskReaderWriter(local_output_path)

        md_writer.write(
            content=json.dumps(content_list, ensure_ascii=False, indent=4),
            path=f"{odl_pdf_name}_content_list.json"
        )

        md_writer.write(
            content=json.dumps([row.__dict__ for row in images], ensure_ascii=False, indent=4),
            path=f"{odl_pdf_name}_images.json"
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
    
        result.data=await read_md_dump(pipe, output_image_path, new_pdf_name, content_list, md_content, file.filename, is_save_local, local_output_path)
    
        # 清除文件夹
        # shutil.rmtree(output_path)
    
        return result
    
    except Exception as e:
        logger.exception(e)
        
        result.code=500
        result.msg="处理异常"
        return result

async def magic_pdf_parse_main_batch(
    parse_method: str="auto"
    ) ->BaseResultModel:
    result=BaseResultModel()

    if not os.path.exists("batch_files"):
        os.makedirs("batch_files")

    print(f"批处理:正在加载本地文件")

    upload_files=[]
    folder_path = 'batch_files' # 读取文件夹中的所有文件 
    for root, dirs, files in os.walk(folder_path): 
        for file_name in files:
            file_path = os.path.join(root, file_name)
            with open(file_path, "rb") as file:
                file_data = file.read()
                file_bytes = io.BytesIO(file_data)
                upload_file = UploadFile(file=file_bytes, filename=file_name)
                upload_files.append(upload_file)

    print(f"批处理:完成加载本地文件")

    print(f"批处理:正在处理本地文件")

    error_file=[]
    total=len(upload_files)
    index=1
    for upload_file in upload_files:
        print(f"批处理:正在处理文件 {upload_file.filename} {index}/{total}")
        magic_pdf_parse_main_result = await magic_pdf_parse_main(upload_file, parse_method, True, "batch_files")
        if magic_pdf_parse_main_result.code != 200:
            error_file.append(upload_file.filename)
            print(f"批处理:异常处理文件 {upload_file.filename} {index}/{total}")
        else:
            print(f"批处理:完成处理文件 {upload_file.filename} {index}/{total}")

        index=index+1

    print(f"批处理:完成处理本地文件")

    return result