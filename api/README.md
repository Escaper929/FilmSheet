# FilmSheet API

FilmSheet 的后端渲染服务，提供 REST API 供手机 App、Web 前端、NAS 部署使用。

## 快速启动

### 本地开发

```bash
cd api
pip install -r requirements.txt
uvicorn main:app --reload --port 8000
```

### Docker

```bash
docker-compose up -d
```

### 飞牛 NAS

```bash
# 在飞牛 NAS 的 Docker 管理中导入 docker-compose.yml
# 或直接运行：
docker run -d --name filmsheet \
  -p 8000:8000 \
  -v $(pwd)/pack_images:/app/pack_images \
  filmsheet-api
```

## API 接口

### POST /render

渲染一张胶片排版图。

**请求体：** `multipart/form-data`

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `images` | files | - | 上传图片（多张） |
| `film_format` | string | "135" | 画幅：135 或 120 |
| `sub_format` | string | "标准 36×24" | 子画幅 |
| `thumb_width` | int | 400 | 缩略图宽度 |
| `columns` | int | 6 | 每行列数 |
| `spacing` | int | 20 | 图片间距 |
| `force_landscape` | bool | true | 强制横向 |
| `processing_mode` | string | "positive" | 成像模式：positive/negative |
| `render_style` | string | "lightbox" | 渲染风格：lightbox/contact_sheet |
| `output_format` | string | "JPG" | 输出格式：JPG/PNG |
| `quality` | int | 95 | 图片质量 |
| `info_roll` | string | "" | 卷号 |
| `info_camera` | string | "" | 相机 |
| `info_film` | string | "" | 胶卷（用于边字） |
| `info_shoot_date` | string | "" | 拍摄日期 |
| `info_dev_date` | string | "" | 冲洗日期 |
| `info_proc` | string | "" | 冲洗方式 |
| `info_lab` | string | "" | 冲洗地点 |
| `info_scanner` | string | "" | 扫描仪 |
| `info_lang` | string | "en" | 标签语言：zh/en |
| `edge_text` | string | "" | 自定义边字 |
| `pack_image_path` | string | "" | 胶卷包装图路径 |
| `pack_position` | string | "left" | 包装图位置 |
| `pack_border_stroke` | bool | true | 包装图描边 |
| `pack_size` | int | 80 | 包装图大小百分比 |
| `perf_mode` | string | "Auto" | 齿孔模式 |
| `signature` | string | "" | 水印签名 |
| `is_preview` | bool | false | 是否为预览（关闭抗锯齿） |

**响应：** 渲染后的图片（JPEG/PNG）

### GET /health

健康检查。

```json
{"status": "ok", "service": "FilmSheet API", "version": "1.6.3"}
```

## 开发

```bash
# 启动开发服务器
uvicorn main:app --reload --port 8000

# 查看 API 文档
# http://localhost:8000/docs
```
