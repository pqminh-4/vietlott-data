# Vietlott Data

Kho dữ liệu mở thu thập kết quả quay số từ các nguồn chính thức của Vietlott,
tự động cập nhật bằng GitHub Actions và phát hành dưới dạng JSON/CSV qua
GitHub Pages.

## Phạm vi v1

| Mã | Sản phẩm | Lịch quay được dùng để chọn workflow |
|---|---|---|
| `mega645` | Mega 6/45 | 18:00 Thứ Tư, Thứ Sáu, Chủ Nhật |
| `power655` | Power 6/55 | 18:00 Thứ Ba, Thứ Năm, Thứ Bảy |
| `lotto535` | Lotto 5/35 | 13:00 và 21:00 hằng ngày |
| `max3d` | Max 3D / Max 3D+ | 18:00 Thứ Hai, Thứ Tư, Thứ Sáu |
| `max3d_pro` | Max 3D Pro | 18:00 Thứ Ba, Thứ Năm, Thứ Bảy |

Keno và Bingo18 chưa nằm trong v1. Lịch chỉ quyết định thời điểm polling; dữ
liệu ngày/kỳ luôn lấy từ phản hồi chính thức thay vì tự suy đoán.

## Kết quả kỳ quay thưởng gần nhất

Đối chiếu nguồn Vietlott lúc **19:28 ngày 03/08/2026 (UTC+7)**. Bảng này là
snapshot tại thời điểm cập nhật README; liên kết ở mã kỳ luôn trỏ tới bản ghi
`latest.json` mới nhất của từng sản phẩm.

| Sản phẩm | Kỳ quay | Kết quả |
|---|---|---|
| Mega 6/45 | [#01544](site/api/v1/mega645/latest.json) · 02/08/2026 | **03 · 12 · 20 · 25 · 27 · 37** |
| Power 6/55 | [#01379](site/api/v1/power655/latest.json) · 01/08/2026 | **11 · 14 · 16 · 44 · 49 · 55** · Số đặc biệt: **39** |
| Lotto 5/35 | [#00801](site/api/v1/lotto535/latest.json) · 13:00 03/08/2026 | **02 · 09 · 12 · 21 · 25** · Số đặc biệt: **10** |
| Max 3D / Max 3D+ | [#01114](site/api/v1/max3d/latest.json) · 03/08/2026 | Đặc biệt: **268, 568**<br>Nhất: **351, 979, 231, 691**<br>Nhì: **973, 102, 063, 537, 359, 727**<br>Ba: **845, 548, 123, 502, 052, 124, 635, 850** |
| Max 3D Pro | [#00760](site/api/v1/max3d_pro/latest.json) · 01/08/2026 | Đặc biệt: **452, 600**<br>Nhất: **484, 745, 243, 256**<br>Nhì: **580, 006, 849, 538, 564, 451**<br>Ba: **571, 794, 881, 129, 804, 686, 941, 374** |

## Cài đặt và sử dụng

Yêu cầu Python 3.12.

```bash
python -m pip install -e ".[dev]"

vietlott collect --games all --latest
vietlott backfill --game all --resume --max-draws 250
vietlott reconcile --games all
vietlott validate
vietlott build-api
vietlott check-freshness --games all --max-delay-minutes 60
vietlott check-freshness --games all --max-delay-minutes 60 \
  --api-base-url https://pqminh-4.github.io/vietlott-data/api/v1
```

`collect --dry-run` thực hiện request, parse và validation nhưng không ghi dữ
liệu. Thêm `--audit-pdf` để đối chiếu bản ghi với PDF chính thức khi Vietlott
trả về liên kết PDF.

## Thu thập bằng self-hosted runner

Vietlott chặn IP datacenter của GitHub-hosted runners. Các workflow truy cập nguồn
chính thức vì vậy chạy trong repository điều khiển private `pqminh-4/vietlott-ops`
trên một self-hosted runner Ubuntu đặt tại Việt Nam. Runner không được đăng ký vào
repository public này; pull request công khai chỉ chạy CI trên GitHub-hosted runner.

Repository điều khiển checkout nhánh `main`, thu thập và validate trực tiếp, sinh lại
`data/` cùng `site/`, rồi dùng installation token ngắn hạn của một GitHub App chỉ có
quyền ghi nội dung repository này để push khi dữ liệu thực sự thay đổi.

Mã Cloudflare Worker không còn được workflow sử dụng. Nó chỉ được giữ để rollback
cho tới khi self-hosted runner và watchdog vượt qua trọn chu kỳ 13:00–18:00–21:00;
sau đó hai Worker, secret và service binding mới được gỡ theo thứ tự đã ghi nhận.

## Bố cục dữ liệu

- `data/canonical/<game>.jsonl`: nguồn dữ liệu chuẩn, một kỳ trên mỗi dòng.
- `data/state/`: checkpoint backfill có thể chạy tiếp.
- `data/coverage/`: phạm vi, kỳ thiếu và các khoảng nguồn không còn cung cấp.
- `data/csv/<game>/`: ba bảng chuẩn hóa `draws`, `results`, `prizes`.
- `site/api/v1/`: API JSON tĩnh và các file CSV tải xuống.

Các endpoint chính sau khi bật GitHub Pages:

```text
/api/v1/index.json
/api/v1/<game>/latest.json
/api/v1/<game>/draws/<draw_id>.json
/api/v1/<game>/years/<yyyy>.json
/api/v1/<game>/coverage.json
/api/v1/downloads/<game>-draws.csv
```

## Tự động hóa trên GitHub

1. Repository public này chỉ chạy CI và deploy GitHub Pages trên GitHub-hosted runner.
2. Repository private `pqminh-4/vietlott-ops` giữ lịch thu thập và smoke test.
3. Self-hosted runner chỉ được đăng ký vào repository private với label `vietlott-vn`.
4. GitHub App ghi dữ liệu chỉ được cài trên `pqminh-4/vietlott-data` với quyền
   `Contents: read/write`; private key chỉ lưu trong secrets của repository private.
5. Chạy thủ công workflow **Collect and publish Vietlott data** với mode `backfill`
   để bắt đầu lịch sử; lượt reconcile 02:17 tiếp tục checkpoint mỗi ngày cho tới
   khi hoàn tất.

Workflow mở cửa sổ polling chính ở phút +7 và cửa sổ dự phòng ở phút +67 sau mỗi
giờ quay theo `Asia/Ho_Chi_Minh`; trong mỗi cửa sổ, collector thử lại mỗi 5 phút đến
khi có dữ liệu hoặc chạm mốc phục hồi +120. SLA chính thức là dữ liệu phải xuất hiện
trên production trong vòng 60 phút. Workflow **Freshness watchdog** chạy độc lập trên
GitHub-hosted runner sau các mốc 13:00, 18:00 và 21:00, kiểm tra riêng API theo `main`
và GitHub Pages, ghi Job Summary rồi thất bại nếu dữ liệu `stale` hoặc `invalid`.
Trước deadline, trạng thái `pending` không tạo cảnh báo. Lượt deploy Pages tự thử lại
một lần nếu backend bị kẹt ở timeout 10 phút; lượt reconcile vẫn tự bù dữ liệu bị lỡ.

## An toàn dữ liệu

- Chỉ chấp nhận URL HTTPS thuộc `vietlott.vn` và `media.vietlott.vn`.
- Phản hồi `403`, `429`, JSON/HTML lỗi hoặc bản ghi sai miền số làm workflow
  thất bại trước khi commit/publish.
- File JSONL được ghi nguyên tử, sắp xếp ổn định và chỉ thay đổi khi nội dung
  chuẩn hóa thay đổi.
- Không có logic dự đoán hoặc khuyến nghị đánh bạc.

Mã nguồn được cấp phép MIT. Dữ liệu giữ nguyên ghi nhận nguồn Vietlott và chưa
được gán giấy phép dữ liệu độc lập.
