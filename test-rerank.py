"""
销售Agent 重排序模型测试脚本（增强调试版）
模型：BAAI/bge-reranker-v2-m3
"""

import sys
import traceback
import time

def check_environment():
    print("========== 环境检查 ==========")
    print(f"Python 版本: {sys.version}")
    try:
        import torch
        print(f"PyTorch 版本: {torch.__version__}")
        print(f"CUDA 可用: {torch.cuda.is_available()}")
        if torch.cuda.is_available():
            print(f"CUDA 版本: {torch.version.cuda}")
            print(f"GPU 名称: {torch.cuda.get_device_name(0)}")
        else:
            print("未检测到 GPU，将使用 CPU（速度会较慢）")
    except ImportError:
        print("❌ PyTorch 未安装！请运行: pip install torch")
        sys.exit(1)

    try:
        from FlagEmbedding import FlagReranker
        print("✅ FlagEmbedding 已安装")
    except ImportError:
        print("❌ FlagEmbedding 未安装！请运行: pip install FlagEmbedding")
        sys.exit(1)
    print("===============================\n")

def main():
    try:
        check_environment()

        # 加载模型
        print("正在加载重排序模型 BAAI/bge-reranker-v2-m3 ...")
        from FlagEmbedding import FlagReranker
        reranker = FlagReranker(
            'BAAI/bge-reranker-v2-m3',
            use_fp16=True
        )
        print("✅ 模型加载完成\n")

        # 测试数据
        query = "我需要一款适合油性皮肤、控油持久的粉底液"
        candidates = [
            "清爽控油妆前乳，适合中性及油性皮肤，可延长底妆持久度。",
            "柔光持妆粉底液，控油配方，24小时不脱妆，专为油皮设计。",
            "保湿精华液，含玻尿酸成分，深层补水，适合干性皮肤。",
            "控油散粉，透明无色，定妆同时吸附多余油脂。",
            "某品牌粉底液，主打高遮瑕、奶油肌妆效，适合所有肤质。",
            "温和洁面乳，适用于敏感肌，可卸除淡妆。",
            "长续航粉底液，控油且不暗沉，尤其适合夏季油皮使用。",
            "滋润型粉底霜，富含养肤成分，推荐干性皮肤使用。"
        ]

        print(f"用户查询: {query}\n")
        pairs = [[query, doc] for doc in candidates]

        print("正在计算相关性分数...")
        start = time.time()
        scores = reranker.compute_score(pairs, normalize=True)
        elapsed = time.time() - start
        print(f"✅ 计算完成，耗时: {elapsed:.3f} 秒\n")

        # 排序输出
        scored = sorted(zip(scores, candidates), key=lambda x: x[0], reverse=True)
        print("========== 重排序结果 ==========")
        for rank, (score, doc) in enumerate(scored, 1):
            print(f"{rank}. [{score:.4f}] {doc}")

    except Exception:
        print("\n❌ 发生异常，完整错误信息如下：")
        traceback.print_exc()

if __name__ == "__main__":
    main()