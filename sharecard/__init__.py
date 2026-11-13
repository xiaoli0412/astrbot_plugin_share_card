"""sharecard —— B 站视频分享卡片子包。

注意：本包一律以「顶层包」方式导入（`from sharecard.ark import ...`），
不走相对导入，避免 AstrBot 以 `data.plugins.xxx.main` 作为模块名加载时
相对导入失败。主入口会先把插件目录塞进 sys.path。
"""

__all__ = ["ark", "search", "compose"]
