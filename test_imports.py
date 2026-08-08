# test_imports.py
"""测试导入 - 验证 core 模块能否正确加载 processor"""

import sys
import os

# 添加项目根目录
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    from processor.film_processor import FilmProcessor
    print("processor.film_processor 导入成功")

    from processor.image_pipeline import process_135_image
    print("processor.image_pipeline 导入成功")

    from processor.renderer import BaseRenderer
    print("processor.renderer 导入成功")

    from utils.helpers import STYLE_COLORS
    print("utils.helpers 导入成功")

    print("\n所有核心模块导入成功！")

except Exception as e:
    print("导入失败: " + str(e))
    import traceback
    traceback.print_exc()
