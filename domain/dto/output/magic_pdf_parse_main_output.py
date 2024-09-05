from pydantic import BaseModel
from typing import List, Dict, Any
import base64

class ImageData(BaseModel):
    name: str
    data: str # base64

class MagicPdfParseMainOutput(BaseModel): 
    model: List[Dict[str, Any]]
    middle: Dict[str, Any]
    content_list: List[Dict[str, Any]]
    md: str
    images: List[ImageData]
    
    
    