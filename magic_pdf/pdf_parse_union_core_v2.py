import copy
import os
import statistics
import time
from typing import List

import torch
from loguru import logger

from magic_pdf.config.drop_reason import DropReason
from magic_pdf.config.enums import SupportedPdfParseMethod
from magic_pdf.config.ocr_content_type import BlockType, ContentType
from magic_pdf.data.dataset import Dataset, PageableData
from magic_pdf.libs.boxbase import calculate_overlap_area_in_bbox1_area_ratio
from magic_pdf.libs.clean_memory import clean_memory
from magic_pdf.libs.commons import fitz, get_delta_time
from magic_pdf.libs.config_reader import get_local_layoutreader_model_dir
from magic_pdf.libs.convert_utils import dict_to_list
from magic_pdf.libs.hash_utils import compute_md5
from magic_pdf.libs.local_math import float_equal
from magic_pdf.libs.pdf_image_tools import cut_image_to_pil_image
from magic_pdf.model.magic_model import MagicModel

os.environ['NO_ALBUMENTATIONS_UPDATE'] = '1'  # 禁止albumentations检查更新
os.environ['YOLO_VERBOSE'] = 'False'  # disable yolo logger

try:
    import torchtext

    if torchtext.__version__ >= "0.18.0":
        torchtext.disable_torchtext_deprecation_warning()
except ImportError:
    pass
from magic_pdf.model.sub_modules.model_init import AtomModelSingleton

from magic_pdf.para.para_split_v3 import para_split
from magic_pdf.pre_proc.citationmarker_remove import remove_citation_marker
from magic_pdf.pre_proc.construct_page_dict import \
    ocr_construct_page_component_v2
from magic_pdf.pre_proc.cut_image import ocr_cut_image_and_table
from magic_pdf.pre_proc.equations_replace import (
    combine_chars_to_pymudict, remove_chars_in_text_blocks,
    replace_equations_in_textblock)
from magic_pdf.pre_proc.ocr_detect_all_bboxes import \
    ocr_prepare_bboxes_for_layout_split_v2
from magic_pdf.pre_proc.ocr_dict_merge import (fill_spans_in_blocks,
                                               fix_block_spans_v2,
                                               fix_discarded_block)
from magic_pdf.pre_proc.ocr_span_list_modify import (
    get_qa_need_list_v2, remove_overlaps_low_confidence_spans,
    remove_overlaps_min_spans)
from magic_pdf.pre_proc.resolve_bbox_conflict import \
    check_useful_block_horizontal_overlap


def remove_horizontal_overlap_block_which_smaller(all_bboxes):
    useful_blocks = []
    for bbox in all_bboxes:
        useful_blocks.append({'bbox': bbox[:4]})
    is_useful_block_horz_overlap, smaller_bbox, bigger_bbox = (
        check_useful_block_horizontal_overlap(useful_blocks)
    )
    if is_useful_block_horz_overlap:
        logger.warning(
            f'skip this page, reason: {DropReason.USEFUL_BLOCK_HOR_OVERLAP}, smaller bbox is {smaller_bbox}, bigger bbox is {bigger_bbox}'
        )  # noqa: E501
        for bbox in all_bboxes.copy():
            if smaller_bbox == bbox[:4]:
                all_bboxes.remove(bbox)

    return is_useful_block_horz_overlap, all_bboxes


def __replace_STX_ETX(text_str: str):
    """Replace \u0002 and \u0003, as these characters become garbled when extracted using pymupdf. In fact, they were originally quotation marks.
    Drawback: This issue is only observed in English text; it has not been found in Chinese text so far.

        Args:
            text_str (str): raw text

        Returns:
            _type_: replaced text
    """  # noqa: E501
    if text_str:
        s = text_str.replace('\u0002', "'")
        s = s.replace('\u0003', "'")
        return s
    return text_str


def chars_to_content(span):
        # # 先给chars按char['bbox']的x坐标排序
        # span['chars'] = sorted(span['chars'], key=lambda x: x['bbox'][0])

        # 先给chars按char['bbox']的中心点的x坐标排序
        span['chars'] = sorted(span['chars'], key=lambda x: (x['bbox'][0] + x['bbox'][2]) / 2)
        content = ''

        # 求char的平均宽度
        if len(span['chars']) == 0:
            span['content'] = content
            del span['chars']
            return
        else:
            char_width_sum = sum([char['bbox'][2] - char['bbox'][0] for char in span['chars']])
            char_avg_width = char_width_sum / len(span['chars'])

        for char in span['chars']:
            # 如果下一个char的x0和上一个char的x1距离超过一个字符宽度，则需要在中间插入一个空格
            if char['bbox'][0] - span['chars'][span['chars'].index(char) - 1]['bbox'][2] > char_avg_width:
                content += ' '
            content += char['c']
        span['content'] = __replace_STX_ETX(content)
        del span['chars']


LINE_STOP_FLAG = ('.', '!', '?', '。', '！', '？', ')', '）', '"', '”', ':', '：', ';', '；', ']', '】', '}', '}', '>', '》', '、', ',', '，', '-', '—', '–',)
def fill_char_in_spans(spans, all_chars):

    for char in all_chars:
        for span in spans:
            # 判断char是否属于LINE_STOP_FLAG
            if char['c'] in LINE_STOP_FLAG:
                char_is_line_stop_flag = True
            else:
                char_is_line_stop_flag = False
            if calculate_char_in_span(char['bbox'], span['bbox'], char_is_line_stop_flag):
                span['chars'].append(char)
                break

    for span in spans:
        chars_to_content(span)


# 使用鲁棒性更强的中心点坐标判断
def calculate_char_in_span(char_bbox, span_bbox, char_is_line_stop_flag):
    char_center_x = (char_bbox[0] + char_bbox[2]) / 2
    char_center_y = (char_bbox[1] + char_bbox[3]) / 2
    span_center_y = (span_bbox[1] + span_bbox[3]) / 2
    span_height = span_bbox[3] - span_bbox[1]

    if (
        span_bbox[0] < char_center_x < span_bbox[2]
        and span_bbox[1] < char_center_y < span_bbox[3]
        and abs(char_center_y - span_center_y) < span_height / 4  # 字符的中轴和span的中轴高度差不能超过1/4span高度
    ):
        return True
    else:
        # 如果char是LINE_STOP_FLAG，就不用中心点判定，换一种方案（左边界在span区域内，高度判定和之前逻辑一致）
        # 主要是给结尾符号一个进入span的机会，这个char还应该离span右边界较近
        if char_is_line_stop_flag:
            if (
                (span_bbox[2] - span_height) < char_bbox[0] < span_bbox[2]
                and char_center_x > span_bbox[0]
                and span_bbox[1] < char_center_y < span_bbox[3]
                and abs(char_center_y - span_center_y) < span_height / 4
            ):
                return True
        else:
            return False


def txt_spans_extract_v2(pdf_page, spans, all_bboxes, all_discarded_blocks, lang):

    useful_spans = []
    unuseful_spans = []
    for span in spans:
        for block in all_bboxes:
            if block[7] in [BlockType.ImageBody, BlockType.TableBody, BlockType.InterlineEquation]:
                continue
            else:
                if calculate_overlap_area_in_bbox1_area_ratio(span['bbox'], block[0:4]) > 0.5:
                    useful_spans.append(span)
                    break
        for block in all_discarded_blocks:
            if calculate_overlap_area_in_bbox1_area_ratio(span['bbox'], block[0:4]) > 0.5:
                unuseful_spans.append(span)
                break

    text_blocks = pdf_page.get_text('rawdict', flags=fitz.TEXTFLAGS_TEXT)['blocks']

    # @todo: 拿到char之后把倾斜角度较大的先删一遍
    all_pymu_chars = []
    for block in text_blocks:
        for line in block['lines']:
            for span in line['spans']:
                all_pymu_chars.extend(span['chars'])

    new_spans = []

    for span in useful_spans:
        if span['type'] in [ContentType.Text]:
            span['chars'] = []
            new_spans.append(span)

    for span in unuseful_spans:
        if span['type'] in [ContentType.Text]:
            span['chars'] = []
            new_spans.append(span)

    fill_char_in_spans(new_spans, all_pymu_chars)

    empty_spans = []
    for span in new_spans:
        if len(span['content']) == 0:
            empty_spans.append(span)
    if len(empty_spans) > 0:

        # 初始化ocr模型
        atom_model_manager = AtomModelSingleton()
        ocr_model = atom_model_manager.get_atom_model(
            atom_model_name="ocr",
            ocr_show_log=False,
            det_db_box_thresh=0.3,
            lang=lang
        )

        for span in empty_spans:
            spans.remove(span)
            # 对span的bbox截图
            span_img = cut_image_to_pil_image(span['bbox'], pdf_page, mode="cv2")
            ocr_res = ocr_model.ocr(span_img, det=False)
            # logger.info(f"ocr_res: {ocr_res}")
            # logger.info(f"empty_span: {span}")
            if ocr_res and len(ocr_res) > 0:
                if len(ocr_res[0]) > 0:
                    ocr_text, ocr_score = ocr_res[0][0]
                    if ocr_score > 0.5 and len(ocr_text) > 0:
                            span['content'] = ocr_text
                            spans.append(span)

    return spans


def txt_spans_extract_v1(pdf_page, inline_equations, interline_equations):
    text_raw_blocks = pdf_page.get_text('dict', flags=fitz.TEXTFLAGS_TEXT)['blocks']
    char_level_text_blocks = pdf_page.get_text('rawdict', flags=fitz.TEXTFLAGS_TEXT)[
        'blocks'
    ]
    text_blocks = combine_chars_to_pymudict(text_raw_blocks, char_level_text_blocks)
    text_blocks = replace_equations_in_textblock(
        text_blocks, inline_equations, interline_equations
    )
    text_blocks = remove_citation_marker(text_blocks)
    text_blocks = remove_chars_in_text_blocks(text_blocks)
    spans = []
    for v in text_blocks:
        for line in v['lines']:
            for span in line['spans']:
                bbox = span['bbox']
                if float_equal(bbox[0], bbox[2]) or float_equal(bbox[1], bbox[3]):
                    continue
                if span.get('type') not in (
                    ContentType.InlineEquation,
                    ContentType.InterlineEquation,
                ):
                    spans.append(
                        {
                            'bbox': list(span['bbox']),
                            'content': __replace_STX_ETX(span['text']),
                            'type': ContentType.Text,
                            'score': 1.0,
                        }
                    )
    return spans


def replace_text_span(pymu_spans, ocr_spans):
    return list(filter(lambda x: x['type'] != ContentType.Text, ocr_spans)) + pymu_spans


def model_init(model_name: str):
    from transformers import LayoutLMv3ForTokenClassification

    if torch.cuda.is_available():
        device = torch.device('cuda')
        if torch.cuda.is_bf16_supported():
            supports_bfloat16 = True
        else:
            supports_bfloat16 = False
    else:
        device = torch.device('cpu')
        supports_bfloat16 = False

    if model_name == 'layoutreader':
        # 检测modelscope的缓存目录是否存在
        layoutreader_model_dir = get_local_layoutreader_model_dir()
        if os.path.exists(layoutreader_model_dir):
            model = LayoutLMv3ForTokenClassification.from_pretrained(
                layoutreader_model_dir
            )
        else:
            logger.warning(
                'local layoutreader model not exists, use online model from huggingface'
            )
            model = LayoutLMv3ForTokenClassification.from_pretrained(
                'hantian/layoutreader'
            )
        # 检查设备是否支持 bfloat16
        if supports_bfloat16:
            model.bfloat16()
        model.to(device).eval()
    else:
        logger.error('model name not allow')
        exit(1)
    return model


class ModelSingleton:
    _instance = None
    _models = {}

    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def get_model(self, model_name: str):
        if model_name not in self._models:
            self._models[model_name] = model_init(model_name=model_name)
        return self._models[model_name]


def do_predict(boxes: List[List[int]], model) -> List[int]:
    from magic_pdf.model.sub_modules.reading_oreder.layoutreader.helpers import (
        boxes2inputs, parse_logits, prepare_inputs)

    inputs = boxes2inputs(boxes)
    inputs = prepare_inputs(inputs, model)
    logits = model(**inputs).logits.cpu().squeeze(0)
    return parse_logits(logits, len(boxes))


def cal_block_index(fix_blocks, sorted_bboxes):

    if sorted_bboxes is not None:
        # 使用layoutreader排序
        for block in fix_blocks:
            line_index_list = []
            if len(block['lines']) == 0:
                block['index'] = sorted_bboxes.index(block['bbox'])
            else:
                for line in block['lines']:
                    line['index'] = sorted_bboxes.index(line['bbox'])
                    line_index_list.append(line['index'])
                median_value = statistics.median(line_index_list)
                block['index'] = median_value

            # 删除图表body block中的虚拟line信息, 并用real_lines信息回填
            if block['type'] in [BlockType.ImageBody, BlockType.TableBody]:
                block['virtual_lines'] = copy.deepcopy(block['lines'])
                block['lines'] = copy.deepcopy(block['real_lines'])
                del block['real_lines']
    else:
        # 使用xycut排序
        block_bboxes = []
        for block in fix_blocks:
            block_bboxes.append(block['bbox'])

            # 删除图表body block中的虚拟line信息, 并用real_lines信息回填
            if block['type'] in [BlockType.ImageBody, BlockType.TableBody]:
                block['virtual_lines'] = copy.deepcopy(block['lines'])
                block['lines'] = copy.deepcopy(block['real_lines'])
                del block['real_lines']

        import numpy as np

        from magic_pdf.model.sub_modules.reading_oreder.layoutreader.xycut import \
            recursive_xy_cut

        random_boxes = np.array(block_bboxes)
        np.random.shuffle(random_boxes)
        res = []
        recursive_xy_cut(np.asarray(random_boxes).astype(int), np.arange(len(block_bboxes)), res)
        assert len(res) == len(block_bboxes)
        sorted_boxes = random_boxes[np.array(res)].tolist()

        for i, block in enumerate(fix_blocks):
            block['index'] = sorted_boxes.index(block['bbox'])

        # 生成line index
        sorted_blocks = sorted(fix_blocks, key=lambda b: b['index'])
        line_inedx = 1
        for block in sorted_blocks:
            for line in block['lines']:
                line['index'] = line_inedx
                line_inedx += 1

    return fix_blocks


def insert_lines_into_block(block_bbox, line_height, page_w, page_h):
    # block_bbox是一个元组(x0, y0, x1, y1)，其中(x0, y0)是左下角坐标，(x1, y1)是右上角坐标
    x0, y0, x1, y1 = block_bbox

    block_height = y1 - y0
    block_weight = x1 - x0

    # 如果block高度小于n行正文，则直接返回block的bbox
    if line_height * 3 < block_height:
        if (
            block_height > page_h * 0.25 and page_w * 0.5 > block_weight > page_w * 0.25
        ):  # 可能是双列结构，可以切细点
            lines = int(block_height / line_height) + 1
        else:
            # 如果block的宽度超过0.4页面宽度，则将block分成3行(是一种复杂布局，图不能切的太细)
            if block_weight > page_w * 0.4:
                line_height = (y1 - y0) / 3
                lines = 3
            elif block_weight > page_w * 0.25:  # （可能是三列结构，也切细点）
                lines = int(block_height / line_height) + 1
            else:  # 判断长宽比
                if block_height / block_weight > 1.2:  # 细长的不分
                    return [[x0, y0, x1, y1]]
                else:  # 不细长的还是分成两行
                    line_height = (y1 - y0) / 2
                    lines = 2

        # 确定从哪个y位置开始绘制线条
        current_y = y0

        # 用于存储线条的位置信息[(x0, y), ...]
        lines_positions = []

        for i in range(lines):
            lines_positions.append([x0, current_y, x1, current_y + line_height])
            current_y += line_height
        return lines_positions

    else:
        return [[x0, y0, x1, y1]]


def sort_lines_by_model(fix_blocks, page_w, page_h, line_height):
    page_line_list = []
    for block in fix_blocks:
        if block['type'] in [
            BlockType.Text, BlockType.Title, BlockType.InterlineEquation,
            BlockType.ImageCaption, BlockType.ImageFootnote,
            BlockType.TableCaption, BlockType.TableFootnote
        ]:
            if len(block['lines']) == 0:
                bbox = block['bbox']
                lines = insert_lines_into_block(bbox, line_height, page_w, page_h)
                for line in lines:
                    block['lines'].append({'bbox': line, 'spans': []})
                page_line_list.extend(lines)
            else:
                for line in block['lines']:
                    bbox = line['bbox']
                    page_line_list.append(bbox)
        elif block['type'] in [BlockType.ImageBody, BlockType.TableBody]:
            bbox = block['bbox']
            block['real_lines'] = copy.deepcopy(block['lines'])
            lines = insert_lines_into_block(bbox, line_height, page_w, page_h)
            block['lines'] = []
            for line in lines:
                block['lines'].append({'bbox': line, 'spans': []})
            page_line_list.extend(lines)

    if len(page_line_list) > 200:  # layoutreader最高支持512line
        return None

    # 使用layoutreader排序
    x_scale = 1000.0 / page_w
    y_scale = 1000.0 / page_h
    boxes = []
    # logger.info(f"Scale: {x_scale}, {y_scale}, Boxes len: {len(page_line_list)}")
    for left, top, right, bottom in page_line_list:
        if left < 0:
            logger.warning(
                f'left < 0, left: {left}, right: {right}, top: {top}, bottom: {bottom}, page_w: {page_w}, page_h: {page_h}'
            )  # noqa: E501
            left = 0
        if right > page_w:
            logger.warning(
                f'right > page_w, left: {left}, right: {right}, top: {top}, bottom: {bottom}, page_w: {page_w}, page_h: {page_h}'
            )  # noqa: E501
            right = page_w
        if top < 0:
            logger.warning(
                f'top < 0, left: {left}, right: {right}, top: {top}, bottom: {bottom}, page_w: {page_w}, page_h: {page_h}'
            )  # noqa: E501
            top = 0
        if bottom > page_h:
            logger.warning(
                f'bottom > page_h, left: {left}, right: {right}, top: {top}, bottom: {bottom}, page_w: {page_w}, page_h: {page_h}'
            )  # noqa: E501
            bottom = page_h

        left = round(left * x_scale)
        top = round(top * y_scale)
        right = round(right * x_scale)
        bottom = round(bottom * y_scale)
        assert (
            1000 >= right >= left >= 0 and 1000 >= bottom >= top >= 0
        ), f'Invalid box. right: {right}, left: {left}, bottom: {bottom}, top: {top}'  # noqa: E126, E121
        boxes.append([left, top, right, bottom])
    model_manager = ModelSingleton()
    model = model_manager.get_model('layoutreader')
    with torch.no_grad():
        orders = do_predict(boxes, model)
    sorted_bboxes = [page_line_list[i] for i in orders]

    return sorted_bboxes


def get_line_height(blocks):
    page_line_height_list = []
    for block in blocks:
        if block['type'] in [
            BlockType.Text, BlockType.Title,
            BlockType.ImageCaption, BlockType.ImageFootnote,
            BlockType.TableCaption, BlockType.TableFootnote
        ]:
            for line in block['lines']:
                bbox = line['bbox']
                page_line_height_list.append(int(bbox[3] - bbox[1]))
    if len(page_line_height_list) > 0:
        return statistics.median(page_line_height_list)
    else:
        return 10


def process_groups(groups, body_key, caption_key, footnote_key):
    body_blocks = []
    caption_blocks = []
    footnote_blocks = []
    for i, group in enumerate(groups):
        group[body_key]['group_id'] = i
        body_blocks.append(group[body_key])
        for caption_block in group[caption_key]:
            caption_block['group_id'] = i
            caption_blocks.append(caption_block)
        for footnote_block in group[footnote_key]:
            footnote_block['group_id'] = i
            footnote_blocks.append(footnote_block)
    return body_blocks, caption_blocks, footnote_blocks


def process_block_list(blocks, body_type, block_type):
    indices = [block['index'] for block in blocks]
    median_index = statistics.median(indices)

    body_bbox = next((block['bbox'] for block in blocks if block.get('type') == body_type), [])

    return {
        'type': block_type,
        'bbox': body_bbox,
        'blocks': blocks,
        'index': median_index,
    }


def revert_group_blocks(blocks):
    image_groups = {}
    table_groups = {}
    new_blocks = []
    for block in blocks:
        if block['type'] in [BlockType.ImageBody, BlockType.ImageCaption, BlockType.ImageFootnote]:
            group_id = block['group_id']
            if group_id not in image_groups:
                image_groups[group_id] = []
            image_groups[group_id].append(block)
        elif block['type'] in [BlockType.TableBody, BlockType.TableCaption, BlockType.TableFootnote]:
            group_id = block['group_id']
            if group_id not in table_groups:
                table_groups[group_id] = []
            table_groups[group_id].append(block)
        else:
            new_blocks.append(block)

    for group_id, blocks in image_groups.items():
        new_blocks.append(process_block_list(blocks, BlockType.ImageBody, BlockType.Image))

    for group_id, blocks in table_groups.items():
        new_blocks.append(process_block_list(blocks, BlockType.TableBody, BlockType.Table))

    return new_blocks


def remove_outside_spans(spans, all_bboxes, all_discarded_blocks):
    def get_block_bboxes(blocks, block_type_list):
        return [block[0:4] for block in blocks if block[7] in block_type_list]

    image_bboxes = get_block_bboxes(all_bboxes, [BlockType.ImageBody])
    table_bboxes = get_block_bboxes(all_bboxes, [BlockType.TableBody])
    other_block_type = []
    for block_type in BlockType.__dict__.values():
        if not isinstance(block_type, str):
            continue
        if block_type not in [BlockType.ImageBody, BlockType.TableBody]:
            other_block_type.append(block_type)
    other_block_bboxes = get_block_bboxes(all_bboxes, other_block_type)
    discarded_block_bboxes = get_block_bboxes(all_discarded_blocks, [BlockType.Discarded])

    new_spans = []

    for span in spans:
        span_bbox = span['bbox']
        span_type = span['type']

        if any(calculate_overlap_area_in_bbox1_area_ratio(span_bbox, block_bbox) > 0.4 for block_bbox in
               discarded_block_bboxes):
            new_spans.append(span)
            continue

        if span_type == ContentType.Image:
            if any(calculate_overlap_area_in_bbox1_area_ratio(span_bbox, block_bbox) > 0.5 for block_bbox in
                   image_bboxes):
                new_spans.append(span)
        elif span_type == ContentType.Table:
            if any(calculate_overlap_area_in_bbox1_area_ratio(span_bbox, block_bbox) > 0.5 for block_bbox in
                   table_bboxes):
                new_spans.append(span)
        else:
            if any(calculate_overlap_area_in_bbox1_area_ratio(span_bbox, block_bbox) > 0.5 for block_bbox in
                   other_block_bboxes):
                new_spans.append(span)

    return new_spans


def parse_page_core(
    page_doc: PageableData, magic_model, page_id, pdf_bytes_md5, imageWriter, parse_mode, lang
):
    need_drop = False
    drop_reason = []

    """从magic_model对象中获取后面会用到的区块信息"""
    img_groups = magic_model.get_imgs_v2(page_id)
    table_groups = magic_model.get_tables_v2(page_id)

    """对image和table的区块分组"""
    img_body_blocks, img_caption_blocks, img_footnote_blocks = process_groups(
        img_groups, 'image_body', 'image_caption_list', 'image_footnote_list'
    )

    table_body_blocks, table_caption_blocks, table_footnote_blocks = process_groups(
        table_groups, 'table_body', 'table_caption_list', 'table_footnote_list'
    )

    discarded_blocks = magic_model.get_discarded(page_id)
    text_blocks = magic_model.get_text_blocks(page_id)
    title_blocks = magic_model.get_title_blocks(page_id)
    inline_equations, interline_equations, interline_equation_blocks = (
        magic_model.get_equations(page_id)
    )

    page_w, page_h = magic_model.get_page_size(page_id)

    """将所有区块的bbox整理到一起"""
    # interline_equation_blocks参数不够准，后面切换到interline_equations上
    interline_equation_blocks = []
    if len(interline_equation_blocks) > 0:
        all_bboxes, all_discarded_blocks = ocr_prepare_bboxes_for_layout_split_v2(
            img_body_blocks, img_caption_blocks, img_footnote_blocks,
            table_body_blocks, table_caption_blocks, table_footnote_blocks,
            discarded_blocks,
            text_blocks,
            title_blocks,
            interline_equation_blocks,
            page_w,
            page_h,
        )
    else:
        all_bboxes, all_discarded_blocks = ocr_prepare_bboxes_for_layout_split_v2(
            img_body_blocks, img_caption_blocks, img_footnote_blocks,
            table_body_blocks, table_caption_blocks, table_footnote_blocks,
            discarded_blocks,
            text_blocks,
            title_blocks,
            interline_equations,
            page_w,
            page_h,
        )

    """获取所有的spans信息"""
    spans = magic_model.get_all_spans(page_id)

    """在删除重复span之前，应该通过image_body和table_body的block过滤一下image和table的span"""
    """顺便删除大水印并保留abandon的span"""
    spans = remove_outside_spans(spans, all_bboxes, all_discarded_blocks)

    """先处理不需要排版的discarded_blocks"""
    discarded_block_with_spans, spans = fill_spans_in_blocks(
        all_discarded_blocks, spans, 0.4
    )
    fix_discarded_blocks = fix_discarded_block(discarded_block_with_spans)

    """如果当前页面没有有效的bbox则跳过"""
    if len(all_bboxes) == 0:
        logger.warning(f'skip this page, not found useful bbox, page_id: {page_id}')
        return ocr_construct_page_component_v2(
            [],
            [],
            page_id,
            page_w,
            page_h,
            [],
            [],
            [],
            interline_equations,
            fix_discarded_blocks,
            need_drop,
            drop_reason,
        )

    """删除重叠spans中置信度较低的那些"""
    spans, dropped_spans_by_confidence = remove_overlaps_low_confidence_spans(spans)
    """删除重叠spans中较小的那些"""
    spans, dropped_spans_by_span_overlap = remove_overlaps_min_spans(spans)

    """根据parse_mode，构造spans，主要是文本类的字符填充"""
    if parse_mode == SupportedPdfParseMethod.TXT:

        """之前的公式替换方案"""
        # pymu_spans = txt_spans_extract_v1(page_doc, inline_equations, interline_equations)
        # spans = replace_text_span(pymu_spans, spans)

        """ocr 中文本类的 span 用 pymu spans 替换！"""
        spans = txt_spans_extract_v2(page_doc, spans, all_bboxes, all_discarded_blocks, lang)

    elif parse_mode == SupportedPdfParseMethod.OCR:
        pass
    else:
        raise Exception('parse_mode must be txt or ocr')

    """对image和table截图"""
    spans = ocr_cut_image_and_table(
        spans, page_doc, page_id, pdf_bytes_md5, imageWriter
    )

    """span填充进block"""
    block_with_spans, spans = fill_spans_in_blocks(all_bboxes, spans, 0.5)

    """对block进行fix操作"""
    fix_blocks = fix_block_spans_v2(block_with_spans)

    """获取所有line并计算正文line的高度"""
    line_height = get_line_height(fix_blocks)

    """获取所有line并对line排序"""
    sorted_bboxes = sort_lines_by_model(fix_blocks, page_w, page_h, line_height)

    """根据line的中位数算block的序列关系"""
    fix_blocks = cal_block_index(fix_blocks, sorted_bboxes)

    """将image和table的block还原回group形式参与后续流程"""
    fix_blocks = revert_group_blocks(fix_blocks)

    """重排block"""
    sorted_blocks = sorted(fix_blocks, key=lambda b: b['index'])

    """获取QA需要外置的list"""
    images, tables, interline_equations = get_qa_need_list_v2(sorted_blocks)

    """构造pdf_info_dict"""
    page_info = ocr_construct_page_component_v2(
        sorted_blocks,
        [],
        page_id,
        page_w,
        page_h,
        [],
        images,
        tables,
        interline_equations,
        fix_discarded_blocks,
        need_drop,
        drop_reason,
    )
    return page_info


def pdf_parse_union(
    dataset: Dataset,
    model_list,
    imageWriter,
    parse_mode,
    start_page_id=0,
    end_page_id=None,
    debug_mode=False,
    lang=None,
):
    pdf_bytes_md5 = compute_md5(dataset.data_bits())

    """初始化空的pdf_info_dict"""
    pdf_info_dict = {}

    """用model_list和docs对象初始化magic_model"""
    magic_model = MagicModel(model_list, dataset)

    """根据输入的起始范围解析pdf"""
    # end_page_id = end_page_id if end_page_id else len(pdf_docs) - 1
    end_page_id = (
        end_page_id
        if end_page_id is not None and end_page_id >= 0
        else len(dataset) - 1
    )

    if end_page_id > len(dataset) - 1:
        logger.warning('end_page_id is out of range, use pdf_docs length')
        end_page_id = len(dataset) - 1

    """初始化启动时间"""
    start_time = time.time()

    for page_id, page in enumerate(dataset):
        """debug时输出每页解析的耗时."""
        if debug_mode:
            time_now = time.time()
            logger.info(
                f'page_id: {page_id}, last_page_cost_time: {get_delta_time(start_time)}'
            )
            start_time = time_now

        """解析pdf中的每一页"""
        if start_page_id <= page_id <= end_page_id:
            page_info = parse_page_core(
                page, magic_model, page_id, pdf_bytes_md5, imageWriter, parse_mode, lang
            )
        else:
            page_info = page.get_page_info()
            page_w = page_info.w
            page_h = page_info.h
            page_info = ocr_construct_page_component_v2(
                [], [], page_id, page_w, page_h, [], [], [], [], [], True, 'skip page'
            )
        pdf_info_dict[f'page_{page_id}'] = page_info

    """分段"""
    para_split(pdf_info_dict)

    """dict转list"""
    pdf_info_list = dict_to_list(pdf_info_dict)
    new_pdf_info_dict = {
        'pdf_info': pdf_info_list,
    }

    clean_memory()

    return new_pdf_info_dict


if __name__ == '__main__':
    pass
