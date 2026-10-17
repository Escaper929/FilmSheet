# -*- coding: utf-8 -*-
"""FilmSheet 银盐册（Archive）。

把「一卷胶片」从一次性渲染任务提升为独立实体：每次生成留下可再次查阅、
可重新出图、可检索的记录。

分层（严格单向依赖，不许回指）：
    models   领域实体，纯数据
    store    持久化，JSON 文件
    service  门面，串起实体与存储
    thumbs   缩略图生成与磁盘缓存

本包不导入tkinter，供桌面端与将来的 API/Web 端共用。
"""