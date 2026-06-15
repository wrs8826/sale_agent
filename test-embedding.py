"""
测试 BAAI/bge-large-zh-v1.5 embedding 模型是否可用
依赖: pip install sentence-transformers
"""

import sys

def test_bge_model():
    try:
        from sentence_transformers import SentenceTransformer
    except ImportError:
        print("错误: 未安装 sentence-transformers 库")
        print("请运行: pip install sentence-transformers")
        sys.exit(1)
    
    model_name = "BAAI/bge-large-zh-v1.5"
    print(f"正在加载模型: {model_name}")
    print("首次运行会从 HuggingFace 下载模型文件，可能需要几分钟...")
    
    try:
        # 加载模型（会自动下载或读取缓存）
        model = SentenceTransformer(model_name)
        print("✅ 模型加载成功")
    except Exception as e:
        print(f"❌ 模型加载失败: {e}")
        print("可能原因: 网络连接问题、HuggingFace 无法访问、模型名称错误")
        sys.exit(1)
    
    # 测试文本（中文）
    test_sentences = ["你好，世界", "这是一个测试句子"]
    
    try:
        # 生成 embedding
        embeddings = model.encode(test_sentences)
        print(f"✅ 成功生成 embedding，形状: {embeddings.shape}")
        print(f"   向量维度: {embeddings.shape[1]}")
        print("模型连通性测试通过！")
    except Exception as e:
        print(f"❌ 编码文本时出错: {e}")
        sys.exit(1)

if __name__ == "__main__":
    test_bge_model()