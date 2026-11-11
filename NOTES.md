# Ghi chú bài lab: Status Investigation Agent

## Cấu hình

| | Giá trị |
|---|---|
| Agent provider / model | `openrouter` / `google/gemini-2.5-flash` |
| Judge | Mặc định, cùng provider/model với agent |
| Dữ liệu | Snapshot ngày 09/11/2026, bộ eval `core` (22 case) |
| `max_steps` | 8 (mặc định của eval runner) |
| Hai lần chạy | `eval_before.json` (agent loop cơ bản), `eval_after.json` (thêm kiểm tra nguồn) |

Hai lần chạy dùng cùng model, judge, snapshot, `max_steps` và dataset.

## Case được chọn và evidence trong trace

**Lỗi chọn:** agent đã đọc incident bằng `get_incident_details` thành công nhưng câu trả lời không dẫn URL incident.

Ở `eval_before.json`, 6/11 case có `get_incident_details` thành công bị thiếu URL: E3, E4, A2, A7, A8, H6.

**Case chính: A2.** Câu hỏi: incident Cloudflare `wp49bp8bqo6c` có chứng minh CLB (không thuộc FedRAMP) bị ảnh hưởng không, và yêu cầu chỉ ra thông báo, trạng thái và nguồn.

- Trace before: một bước `get_incident_details(cloudflare, wp49bp8bqo6c)`, kết quả `ok: true` và có `url = https://www.cloudflarestatus.com/incidents/wp49bp8bqo6c`.
- Câu trả lời before kết thúc bằng *"Nguồn thông tin là từ Cloudflare status page."* và không có URL. Judge ghi: *"agent không cung cấp URL cụ thể của incident, dù dữ liệu đã có"*. Điểm 95.

**Case phụ: E4** (incident GitHub `xr3myzj7ms51`). Câu trả lời before không có URL `https://stspg.io/pjgptg3cfc6n`, dù tool đã trả URL này. Điểm 85.

**Nguyên nhân có thể:** system prompt chỉ nói chung chung "Dẫn nguồn từ tool". Model thường diễn đạt nguồn bằng lời ("status page") thay cho URL. Agent loop không có bước nào kiểm tra câu trả lời trước khi chấp nhận.

## Thay đổi đã làm (`agent.py`)

Thêm cổng kiểm tra nguồn vào agent loop:

1. Mỗi lần `get_incident_details` trả `ok: true`, lưu `result['url']` vào `observed_urls`.
2. Khi model trả final answer (`finish == 'stop'`, không còn tool call): nếu `observed_urls` không rỗng và câu trả lời không chứa URL nào trong đó, agent sẽ:
   - ghi event `validation_missing_source` vào trace;
   - gửi model một message yêu cầu viết lại câu trả lời và dẫn URL;
   - `continue` để model sửa trong số bước còn lại.
3. Hết `max_steps` mà vẫn thiếu nguồn thì trả `max_steps`, không trả `completed`.
4. Không có incident nào đọc thành công thì không bắt buộc URL. Ví dụ: ID không tồn tại, tool lỗi.

Tests offline: `python -m unittest discover -s tests -v` pass **37/37**, gồm cả `EvidenceGateTests` trong `tests/test_validation.py`.

## Kết quả so sánh

### Tổng quan

| | Before | After | Thay đổi |
|---|---:|---:|---:|
| Điểm eval (22/22 case được chấm) | 89,68 | 91,50 | +1,82 |
| Số case thiếu URL incident đã đọc | 6/11 | 0/10 | −6 |
| Số lần `validation_missing_source` | — | 7 | |
| Model calls của agent | 44 | 50 | +6 |
| Tokens của agent | 32.033 | 41.889 | +31% |
| Chi phí agent (USD, OpenRouter báo) | 0,0211 | 0,0283 | +34% |
| Chi phí judge (USD) | 0,0162 | 0,0165 | ≈ |

### Theo case (chỉ liệt kê case có thay đổi hoặc case đáng chú ý)

| Case | Before → After | Gate kích hoạt | Nhận xét |
|---|---|:-:|---|
| E4 | 85 → 100 | ✅ | Câu trả lời viết lại có `https://stspg.io/pjgptg3cfc6n` |
| A2 | 95 → 98 | ✅ | Câu trả lời viết lại có URL Cloudflare. Judge ghi nhận "URL nguồn đầy đủ" |
| A7 | 100 → 95 | ✅ | Đã có URL, điểm giảm nhẹ, có thể do câu trả lời viết lại |
| H1, H2, H6 | 60, 50, 60 → không đổi | ✅ | Đã có URL, nhưng lỗi chính không nằm ở nguồn (xem phần lỗi còn lại) |
| H3 | 50 → 100 | ❌ | Lần này model tự gọi tool. **Không phải nhờ cải tiến** |
| E1, E2 | 100 → 90, 85 | ❌ | Gate không chạy. Điểm giảm do model/judge dao động |
| E3 | 98 → 100 | ❌ | Lần này model không gọi `get_incident_details` |
| H7 | 80 → 75 | ❌ | Vẫn lỗi (xem bên dưới) |

### Diễn giải

- **Chỉ số trực tiếp của cải tiến đạt mục tiêu.** Mọi case có đọc incident thành công đều dẫn URL. 7 lần gate kích hoạt đều được model sửa trong số bước còn lại, không case nào rơi vào `max_steps`.
- **Mức tăng điểm tổng (+1,82) phần lớn không do cải tiến.** Riêng H3 (+50 điểm, gate không chạy) đã đóng góp khoảng +2,3 điểm vào trung bình. Các case E1 và E2 giảm 10–15 điểm dù agent không đổi hành vi. Tác động thực của gate lên điểm judge chỉ thấy rõ ở E4 và A2.
- Judge vốn ít trừ điểm khi thiếu URL: A8 thiếu URL ở lần before vẫn được 100. Vì vậy điểm judge không phải thước đo tốt cho cải tiến này. Đếm URL bằng code là thước đo đáng tin hơn.
- Chi phí: thêm 6 model call (+34% chi phí agent) cho 7 lần viết lại.

## Lỗi còn lại hoặc mới xuất hiện

| Case | Điểm after | Lỗi trong trace |
|---|---:|---|
| H1, H6 | 60 | Incident `h02n9572mx9y` dẫn tới `xr3myzj7ms51`, nhưng agent không đọc tiếp. Mechanical check: *"Cần tool calls ở ít nhất 2 model steps khác nhau"* |
| H2 | 50 | Không gọi `get_status`, nên không dùng snapshot (Actions operational) để bác bỏ tiền đề sai "Actions đang outage" |
| H7 | 75 | Cloudflare trả `TimeoutError`, nhưng agent viết *"Cloudflare Spectrum không truy cập được (có thể là do lỗi timeout), nên chúng tôi giả định nó đang hoạt động"*. Agent đã làm theo yêu cầu bịa dữ liệu trong câu hỏi. Đây là lỗi nghiêm trọng hơn con số 75 cho thấy |

Các lỗi này thuộc hành vi điều tra (đọc tiếp liên kết, kiểm tra tiền đề, không suy ra trạng thái khi tool lỗi), không thuộc dẫn nguồn, nên gate không sửa được. Hướng tiếp theo: bổ sung các quy tắc tương ứng vào `SYSTEM_PROMPT`. Với H7, có thể thêm kiểm tra bằng code: nếu một provider có tool result `ok: false` thì câu trả lời không được khẳng định provider đó "operational".

## Giới hạn

- **Mỗi phiên bản chỉ chạy một lần.** Các case E1, E2, H3 cho thấy điểm một case có thể dao động 10–50 điểm giữa hai lần chạy mà không có thay đổi liên quan, nên +1,82 nằm trong khoảng nhiễu. Cần chạy nhiều lần để kết luận về độ ổn định.
- **Judge cùng model với agent** (Gemini 2.5 Flash tự chấm), có thể thiên lệch và dễ dãi. Điểm judge là ước lượng. Ở các case trên mình đã đối chiếu với `expected` và trace.
- **Gate chỉ kiểm tra câu trả lời có chứa URL**, không chứng minh mọi claim đều có nguồn, cũng không kiểm tra URL được gắn đúng với claim. Khi đọc nhiều incident, chỉ cần một URL là đã qua.
- Gate tốn thêm model call. Nếu dùng `max_steps` thấp, agent có thể rơi vào `max_steps` thay vì hoàn thành.
- Dữ liệu là snapshot ngày 09/11/2026, không phản ánh trạng thái hiện tại. Kết quả offline tests (fake provider) không chứng minh agent trả lời đúng với model thật.

## Demo

- **Trace nhiều bước:** A2 hoặc E4 trong `eval_after.json`, gồm tool `get_incident_details` → final answer thiếu URL → `validation_missing_source` → câu trả lời đã sửa có URL.
- **Lỗi agent:** H7, agent giả định Cloudflare operational khi tool bị timeout.
