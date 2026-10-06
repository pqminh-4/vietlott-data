Các fixture `*-detail.json` là phản hồi AjaxPro chính thức lấy ngày 06/10/2026
từ `Game645ResultDetailWebPart`, `Game655ResultDetailWebPart`,
`Game535ResultDetailWebPart`, `GameMax3DResultDetailWebPart` và
`GameMax3DProResultDetailWebPart` tại `https://www.vietlott.vn/ajaxpro/`.

Phương thức: `ServerSideDrawResult`; mã kỳ tương ứng: 01571, 01407, 00930,
01141, 00788. `RetExtraParam1` chứa kết quả, `RetExtraParam2` chứa bảng giải,
`RetExtraParam3` chứa mã kỳ. Nội dung được giữ để kiểm tra số 0 đầu, bảng giải
và đối chiếu danh sách/chi tiết mà không truy cập mạng trong CI.
