# Bangkok Metro Pathfinder

Hệ thống tìm đường tàu điện Bangkok sử dụng thuật toán **A\*** trên time-expanded graph.

- 194 ga · 11 tuyến · 186 đoạn · 19 điểm giao
- Backend: **Flask 3.1.3** (Python)
- Frontend: **Vanilla JS + Leaflet.js 1.9.4**
- Dữ liệu: GeoJSON + JSON tùy chỉnh

---

## Cài đặt & chạy

```bash
pip install -r requirements.txt
python app.py
```

Mở trình duyệt: [http://localhost:5000](http://localhost:5000)

---

## Tính năng

| Tính năng | Mô tả |
|-----------|-------|
| Tìm đường A\* | Tìm lộ trình ngắn nhất hoặc ít đổi tàu nhất |
| Lộ trình thay thế | Hiển thị cả 2 lộ trình để so sánh |
| Tính giá vé | Tự động tính giá theo từng nhà khai thác (BTS / MRT / ARL / SRT) |
| Giờ khởi hành | Chọn giờ + phút cụ thể, tự động điều chỉnh thời gian chờ theo khung giờ |
| Ước tính giờ đến | Hiển thị giờ đến dự kiến sau khi tìm đường |
| Xử lý vượt khung giờ | Tính lại thời gian chờ đúng nếu hành trình vượt qua 9:00 / 17:00 / 20:00 |
| Admin panel | Chặn đoạn tuyến / đóng ga để mô phỏng bảo trì |

---

## Cấu trúc dự án

```
metro_simple/
├── app.py              # Backend Flask — A*, API endpoints, fare logic
├── index.html          # Frontend — bản đồ Leaflet + sidebar tìm đường
├── requirements.txt    # Flask==3.1.3, flask-cors==6.0.2
└── data/
    ├── bangkok-metro-v2.json   # Dữ liệu ga, tuyến, đoạn, lịch tàu
    ├── railways.geojson        # Tọa độ đường ray cho bản đồ
    └── stations.geojson        # Tọa độ ga chính xác (nguồn OSM)
```

---

## Các tuyến hỗ trợ

| Tuyến | Ký hiệu | Giá vé |
|-------|---------|--------|
| BTS Sukhumvit | `sukhumvit` | 17–65 ฿ |
| BTS Silom | `silom` | (gộp vé BTS) |
| BTS Gold Line | `gold` | 15 ฿ |
| MRT Blue Line | `blue` | 17–45 ฿ |
| MRT Purple Line | `purple` | 14–42 ฿ |
| MRT Yellow Line | `yellow` | 15–45 ฿ |
| MRT Pink Line | `pink` / `pink_mt` | 15–45 ฿ |
| Airport Rail Link | `arl` | 15–45 ฿ |
| SRT Dark Red | `dark_red` | 14–42 ฿ |
| SRT Light Red | `light_red` | 14–42 ฿ |
