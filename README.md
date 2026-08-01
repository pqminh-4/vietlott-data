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

## Cài đặt và sử dụng

Yêu cầu Python 3.12.

```bash
python -m pip install -e ".[dev]"

vietlott collect --games all --latest
vietlott backfill --game all --resume --max-draws 250
vietlott reconcile --games all
vietlott validate
vietlott build-api
```

`collect --dry-run` thực hiện request, parse và validation nhưng không ghi dữ
liệu. Thêm `--audit-pdf` để đối chiếu bản ghi với PDF chính thức khi Vietlott
trả về liên kết PDF.

## Cloudflare Worker relay

Vietlott chặn IP datacenter của GitHub-hosted runners. Worker trong
`workers/relay/` chuyển tiếp request từ GitHub Actions tới đúng nguồn Vietlott
chính thức. Relay không phải proxy mở: nó yêu cầu bearer secret, chỉ cho phép
HTTPS tới `vietlott.vn`, `www.vietlott.vn` và `media.vietlott.vn`, đồng thời
giới hạn method và path cần thiết cho AJAX, trang chi tiết và PDF.

Yêu cầu Node.js 24. Thiết lập cục bộ và deploy:

```bash
npm ci
npm run worker:types
npm run worker:check
npm run worker:test
npx wrangler secret put RELAY_TOKEN --config workers/relay/wrangler.jsonc
npm run worker:deploy
```

Trong repository GitHub, tạo hai Actions secrets:

- `VIETLOTT_RELAY_URL`: URL `https://...workers.dev` của Worker.
- `VIETLOTT_RELAY_TOKEN`: cùng giá trị với secret `RELAY_TOKEN` trên Worker.

Không commit token vào source, `wrangler.jsonc`, `.env` hoặc `.dev.vars`.

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

1. Tạo repository public và push mã nguồn này lên nhánh mặc định `main`.
2. Vào **Settings → Pages → Build and deployment**, chọn **GitHub Actions**.
3. Đảm bảo workflow được phép ghi `contents` và deploy Pages. Repository có
   branch protection phải cho phép `GITHUB_TOKEN` của workflow đẩy commit dữ
   liệu, hoặc cấu hình một nhánh dữ liệu tương đương.
4. Deploy Cloudflare relay và cấu hình hai Actions secrets như phần trên.
5. Chạy thủ công workflow **Collect and publish Vietlott data** với mode
   `backfill` để bắt đầu lịch sử; lượt reconcile 02:17 tiếp tục checkpoint mỗi
   ngày cho tới khi hoàn tất.

Workflow polling chạy ở phút 07, 22, 37 và 52 quanh các giờ quay theo
`Asia/Ho_Chi_Minh`. Mục tiêu cập nhật trong 30 phút là best-effort vì scheduled
workflow của GitHub có thể bị trì hoãn khi hệ thống tải cao. Lượt reconcile sẽ
tự bù dữ liệu bị lỡ.

## An toàn dữ liệu

- Chỉ chấp nhận URL HTTPS thuộc `vietlott.vn` và `media.vietlott.vn`.
- Phản hồi `403`, `429`, JSON/HTML lỗi hoặc bản ghi sai miền số làm workflow
  thất bại trước khi commit/publish.
- File JSONL được ghi nguyên tử, sắp xếp ổn định và chỉ thay đổi khi nội dung
  chuẩn hóa thay đổi.
- Không có logic dự đoán hoặc khuyến nghị đánh bạc.

Mã nguồn được cấp phép MIT. Dữ liệu giữ nguyên ghi nhận nguồn Vietlott và chưa
được gán giấy phép dữ liệu độc lập.
