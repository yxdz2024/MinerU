from pathlib import Path
import shutil
from typing import List
from fastapi import UploadFile
from fastapi.responses import JSONResponse
import os
import json
import io
import uuid
from typing import List, Optional
from glob import glob
from base64 import b64encode

from configs.base_config import MAGIC_PDF_IMG_URL

from domain.dto.base_dto import BaseResultModel
from domain.dto.output.magic_pdf_parse_main_output import ImageData, MagicPdfParseMainOutput

from mineru.utils.enum_class import MakeMode
from mineru.cli.common import aio_do_parse, read_fn, pdf_suffixes, image_suffixes

from loguru import logger

async def read_md_dump(output_image_path, content_list)-> MagicPdfParseMainOutput: 
    # 读取图片
    images = []
    if os.path.exists(output_image_path):
        for filename in os.listdir(output_image_path):
            file_path = os.path.join(output_image_path, filename)
            if os.path.isfile(file_path):
                with open(file_path, 'rb') as file:
                    file_data = file.read()
                    output_image_path_url=output_image_path.replace("\\", "/")
                    url = f"{MAGIC_PDF_IMG_URL}/{output_image_path_url}/{filename}"
                    image_data = ImageData(name=filename, url=url)
                    images.append(image_data)

    de_content_list = json.loads(content_list)

    return MagicPdfParseMainOutput(content_list=de_content_list, images=images)         

def encode_image(image_path: str) -> str:
    """Encode image using base64"""
    with open(image_path, "rb") as f:
        return b64encode(f.read()).decode()

def get_infer_result(file_suffix_identifier: str, pdf_name: str, parse_dir: str) -> Optional[str]:
    """从结果文件中读取推理结果"""
    result_file_path = os.path.join(parse_dir, f"{pdf_name}{file_suffix_identifier}")
    if os.path.exists(result_file_path):
        with open(result_file_path, "r", encoding="utf-8") as fp:
            return fp.read()
    return None

async def magic_pdf_parse_main(
    file:UploadFile,
    parse_method: str="auto",
    is_save_local: bool=False,
    local_output_path: str=None,
    lang_list: list[str] = ["ch"],
    backend="pipeline",
    formula_enable=True,
    table_enable=True,
    server_url=None,
    return_md=True,
    return_middle_json=True,
    return_model_output=True,
    return_content_list=True,
    return_images=False,
    start_page_id=0,
    end_page_id=99999,
    config={}
    ) ->BaseResultModel:

    """
    执行从 pdf 转换到 json、md 的过程，输出 md 和 json 文件到 pdf 文件所在的目录
    :param file: .pdf 文件的路径，可以是相对路径，也可以是绝对路径
    :param parse_method: 解析方法， 共 auto、ocr、txt 三种，默认 auto，如果效果不好，可以尝试 ocr
    :param is_save_local: 是否把输出的内容保存在本地
    :param local_output_path: 本地保存地址
    :param p_lang_list: List of languages for each PDF, default is 'ch' (Chinese)
    :param backend: The backend for parsing PDF, default is 'pipeline'
    :param formula_enable: Enable formula parsing
    :param table_enable: Enable table parsing
    :param server_url: Server URL for vlm-sglang-client backend
    :param return_md: Whether to dump markdown files
    :param return_middle_json: Whether to dump middle JSON files
    :param return_model_output: Whether to dump model output files
    :param return_content_list: Whether to dump content list files
    :param return_images: 是否返回base64图片
    :param start_page_id: Start page ID for parsing, default is 0
    :param end_page_id: End page ID for parsing, default is None (parse all pages until the end of the document)
    :param config: 启动配置
    """

    result=BaseResultModel()

    try:
        # 创建唯一的输出目录
        if not os.path.exists("temp_files"):
            os.makedirs("temp_files")
        if not local_output_path:
            local_output_path="temp_files"

        unique_dir = os.path.join(local_output_path, str(uuid.uuid4()))
        os.makedirs(unique_dir, exist_ok=True)

        # 处理上传的PDF文件
        pdf_file_names = []
        pdf_bytes_list = []

        content = await file.read()
        file_path = Path(file.filename)

        # 如果是图像文件或PDF，使用read_fn处理
        if file_path.suffix.lower() in pdf_suffixes + image_suffixes:
            # 创建临时文件以便使用read_fn
            temp_path = Path(unique_dir) / file_path.name
            with open(temp_path, "wb") as f:
                f.write(content)

            try:
                pdf_bytes = read_fn(temp_path)
                pdf_bytes_list.append(pdf_bytes)
                pdf_file_names.append(file_path.stem)
                os.remove(temp_path)  # 删除临时文件
            except Exception as e:
                logger.exception(f"Failed to load file: {str(e)}")
        
                result.code=500
                result.msg="处理异常"
                return result
        else:
            logger.exception(f"Unsupported file type: {file_path.suffix}")
        
            result.code=500
            result.msg="处理异常"
            return result

        # 设置语言列表，确保与文件数量一致
        actual_lang_list = lang_list
        if len(actual_lang_list) != len(pdf_file_names):
            # 如果语言列表长度不匹配，使用第一个语言或默认"ch"
            actual_lang_list = [actual_lang_list[0] if actual_lang_list else "ch"] * len(pdf_file_names)

        # 调用异步处理函数
        await aio_do_parse(
            output_dir=unique_dir,
            pdf_file_names=pdf_file_names,
            pdf_bytes_list=pdf_bytes_list,
            p_lang_list=actual_lang_list,
            backend=backend,
            parse_method=parse_method,
            formula_enable=formula_enable,
            table_enable=table_enable,
            server_url=server_url,
            f_draw_layout_bbox=True,
            f_draw_span_bbox=True,
            f_dump_md=return_md,
            f_dump_middle_json=return_middle_json,
            f_dump_model_output=return_model_output,
            f_dump_orig_pdf=True,
            f_dump_content_list=return_content_list,
            start_page_id=start_page_id,
            end_page_id=end_page_id,
            **config
        )

        # 构建结果路径
        result_dict = {}
        for pdf_name in pdf_file_names:
            result_dict[pdf_name] = {}
            data = result_dict[pdf_name]

            if backend.startswith("pipeline"):
                parse_dir = os.path.join(unique_dir, pdf_name, parse_method)
            else:
                parse_dir = os.path.join(unique_dir, pdf_name, "vlm")

            if os.path.exists(parse_dir):
                if return_md:
                    data["md_content"] = get_infer_result(".md", pdf_name, parse_dir)
                if return_middle_json:
                    data["middle_json"] = get_infer_result("_middle.json", pdf_name, parse_dir)
                if return_model_output:
                    if backend.startswith("pipeline"):
                        data["model_output"] = get_infer_result("_model.json", pdf_name, parse_dir)
                    else:
                        data["model_output"] = get_infer_result("_model_output.txt", pdf_name, parse_dir)
                if return_content_list:
                    data["content_list"] = get_infer_result("_content_list.json", pdf_name, parse_dir)
                if return_images:
                    image_paths = glob(f"{parse_dir}/images/*.jpg")
                    data["images"] = {
                        os.path.basename(
                            image_path
                        ): f"data:image/jpeg;base64,{encode_image(image_path)}"
                        for image_path in image_paths
                    }

                # 是否要删除处理文件
                if is_save_local==False:
                    result_file_path = os.path.join(parse_dir, f"{pdf_name}.md")
                    if os.path.exists(result_file_path):
                        os.remove(result_file_path)

                    result_file_path = os.path.join(parse_dir, f"{pdf_name}_middle.json")
                    if os.path.exists(result_file_path):
                        os.remove(result_file_path)

                    result_file_path = os.path.join(parse_dir, f"{pdf_name}_model.json")
                    if os.path.exists(result_file_path):
                        os.remove(result_file_path)

                    result_file_path = os.path.join(parse_dir, f"{pdf_name}.pdf")
                    if os.path.exists(result_file_path):
                        os.remove(result_file_path)

                    result_file_path = os.path.join(parse_dir, f"{pdf_name}_model_output.txt")
                    if os.path.exists(result_file_path):
                        os.remove(result_file_path)

                    result_file_path = os.path.join(parse_dir, f"{pdf_name}_content_list.json")
                    if os.path.exists(result_file_path):
                        os.remove(result_file_path)

                    result_file_path = os.path.join(parse_dir, f"{pdf_name}_layout.pdf")
                    if os.path.exists(result_file_path):
                        os.remove(result_file_path)

                    result_file_path = os.path.join(parse_dir, f"{pdf_name}_origin.pdf")
                    if os.path.exists(result_file_path):
                        os.remove(result_file_path)
    
                    result_file_path = os.path.join(parse_dir, f"{pdf_name}_span.pdf")
                    if os.path.exists(result_file_path):
                        os.remove(result_file_path)

        result.data=await read_md_dump(f"{parse_dir}/images", data["content_list"])

        return result
    
    except Exception as e:
        logger.exception(e)
        
        result.code=500
        result.msg="处理异常"
        return result

async def magic_pdf_parse_main2(file:UploadFile,
    parse_method: str="auto",
    is_save_local: bool=False,
    local_output_path: str=None,
    lang_list: list[str] = ["ch"],
    backend="pipeline",
    formula_enable=True,
    table_enable=True,
    server_url=None,
    return_md=True,
    return_middle_json=True,
    return_model_output=True,
    return_content_list=True,
    return_images=False,
    start_page_id=0,
    end_page_id=99999,
    config={}
    ) ->BaseResultModel:

    """
    执行从 pdf 转换到 json、md 的过程，输出 md 和 json 文件到 pdf 文件所在的目录
    :param file: .pdf 文件的路径，可以是相对路径，也可以是绝对路径
    :param parse_method: 解析方法， 共 auto、ocr、txt 三种，默认 auto，如果效果不好，可以尝试 ocr
    :param is_save_local: 是否把输出的内容保存在本地
    :param local_output_path: 本地保存地址
    :param p_lang_list: List of languages for each PDF, default is 'ch' (Chinese)
    :param backend: The backend for parsing PDF, default is 'pipeline'
    :param formula_enable: Enable formula parsing
    :param table_enable: Enable table parsing
    :param server_url: Server URL for vlm-sglang-client backend
    :param return_md: Whether to dump markdown files
    :param return_middle_json: Whether to dump middle JSON files
    :param return_model_output: Whether to dump model output files
    :param return_content_list: Whether to dump content list files
    :param return_images: 是否返回base64图片
    :param start_page_id: Start page ID for parsing, default is 0
    :param end_page_id: End page ID for parsing, default is None (parse all pages until the end of the document)
    :param config: 启动配置
    """

    result=BaseResultModel()
    try:
        if not local_output_path:
            local_output_path = 'temp_files' # 读取文件夹中的所有文件


        pdf_name = file.filename

        name_without_suff = Path(pdf_name).stem

        #文件输出的目录
        output_path = os.path.join(local_output_path, name_without_suff)
        #图片输出的目录
        output_image_path = os.path.join(output_path, 'images')

        pdf_bytes = await file.read()  # 读取 pdf 文件的二进制数据

        # 如果目标目录不存在，则创建目录
        if not os.path.exists(output_path):
            os.makedirs(output_path)
        
        #把文件复制到目标文件夹
        copy_file_path=os.path.join(output_path,pdf_name)

        # 写入本地文件
        with open(copy_file_path, "wb") as f:
            f.write(pdf_bytes)

        # 处理上传的PDF文件
        pdf_file_names = []
        pdf_bytes_list = []

        content = await file.read()
        file_path = Path(file.filename)

        pdf_bytes_list.append(pdf_bytes)
        pdf_file_names.append(Path(copy_file_path).stem)

        # 设置语言列表，确保与文件数量一致
        actual_lang_list = lang_list
        if len(actual_lang_list) != len(pdf_file_names):
            # 如果语言列表长度不匹配，使用第一个语言或默认"ch"
            actual_lang_list = [actual_lang_list[0] if actual_lang_list else "ch"] * len(pdf_file_names)

        # 调用异步处理函数
        await aio_do_parse(
            output_dir=output_path,
            pdf_file_names=pdf_file_names,
            pdf_bytes_list=pdf_bytes_list,
            p_lang_list=actual_lang_list,
            backend=backend,
            parse_method=parse_method,
            formula_enable=formula_enable,
            table_enable=table_enable,
            server_url=server_url,
            f_draw_layout_bbox=True,
            f_draw_span_bbox=True,
            f_dump_md=return_md,
            f_dump_middle_json=return_middle_json,
            f_dump_model_output=return_model_output,
            f_dump_orig_pdf=True,
            f_dump_content_list=return_content_list,
            start_page_id=start_page_id,
            end_page_id=end_page_id,
            **config
        )

        # 构建结果路径
        for pdf_name in pdf_file_names:
            if backend.startswith("pipeline"):
                parse_dir = os.path.join(output_path, pdf_name, parse_method)
            else:
                parse_dir = os.path.join(output_path, pdf_name, "vlm")

            if os.path.exists(parse_dir):
                # 是否要删除处理文件
                if is_save_local==False:
                    result_file_path = os.path.join(parse_dir, f"{pdf_name}.md")
                    if os.path.exists(result_file_path):
                        os.remove(result_file_path)

                    result_file_path = os.path.join(parse_dir, f"{pdf_name}_middle.json")
                    if os.path.exists(result_file_path):
                        os.remove(result_file_path)

                    result_file_path = os.path.join(parse_dir, f"{pdf_name}_model.json")
                    if os.path.exists(result_file_path):
                        os.remove(result_file_path)

                    result_file_path = os.path.join(parse_dir, f"{pdf_name}.pdf")
                    if os.path.exists(result_file_path):
                        os.remove(result_file_path)

                    result_file_path = os.path.join(parse_dir, f"{pdf_name}_model_output.txt")
                    if os.path.exists(result_file_path):
                        os.remove(result_file_path)

                    result_file_path = os.path.join(parse_dir, f"{pdf_name}_content_list.json")
                    if os.path.exists(result_file_path):
                        os.remove(result_file_path)

                    result_file_path = os.path.join(parse_dir, f"{pdf_name}_layout.pdf")
                    if os.path.exists(result_file_path):
                        os.remove(result_file_path)

                    result_file_path = os.path.join(parse_dir, f"{pdf_name}_origin.pdf")
                    if os.path.exists(result_file_path):
                        os.remove(result_file_path)
    
                    result_file_path = os.path.join(parse_dir, f"{pdf_name}_span.pdf")
                    if os.path.exists(result_file_path):
                        os.remove(result_file_path)

                # 复制 parse_dir 中的所有内容到 output_path
                for item in os.listdir(parse_dir):
                    src = os.path.join(parse_dir, item)
                    dst = os.path.join(output_path, item)
                    
                    # 如果目标文件已存在，可以选择覆盖或跳过（这里选择覆盖）
                    if os.path.exists(dst):
                        if os.path.isdir(dst):
                            shutil.rmtree(dst)  # 删除目标文件夹（如果是目录）
                        else:
                            os.remove(dst)  # 删除目标文件（如果是文件）
                    
                    if os.path.isdir(src):
                        shutil.copytree(src, dst)  # 递归复制目录
                    else:
                        shutil.copy2(src, dst)  # 复制文件并保留元数据
                
                # 级联删除 parse_dir
                shutil.rmtree(parse_dir)

        return result
    
    except Exception as e:
        logger.exception(e)
        
        result.code=500
        result.msg="处理异常"
        return result

async def upload(file:UploadFile):
    

    return magic_pdf_parse_main2()



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
            if file_name.lower().endswith(".pdf") and "_layout" not in file_name:
                name_without_suff = Path(file_name).stem
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