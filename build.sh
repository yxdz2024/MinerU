#!/bin/bash

echo "使用 Python: $(python --version)"

# 编译除 aiapi 外的所有 .py 文件，生成 .pyc 放在源目录
find . -type f -name "*.py" ! -path "./aiapi/*" | while read file; do
    python -m compileall -b -f "$file"
done

# 删除原始 .py 文件
find . -type f -name "*.py" ! -path "./aiapi/*" | while read file; do
    rm -f "$file"
done

echo "完成：已编译除 aiapi 外的 .py 文件，保留 .pyc 在原目录。"