# Kế hoạch: agent-wallet (FinTechathon 2026, Topic F)

To-do nằm ở GitHub Issues của repo này. File này chỉ giữ mục tiêu, phạm vi và các mốc. Sửa file qua PR; người dùng merge là đã duyệt.

## Mục tiêu
Nộp hồ sơ đủ 6 mục cho Topic F (Agent Wallet) trước **20/10/2026 23:59 giờ Bắc Kinh (22:59 giờ Việt Nam)**: một AI wallet agent chỉ hành động trong bản chính sách đầu tư (IPS) do chủ ví đặt, có tx hash thật trên Base Sepolia, có bộ eval chứng minh agent tuân thủ. Điểm: task completion 40%, security và compliance 30%, innovation và interaction 30%, bonus on-chain 0 đến 5.

## Phạm vi
- Làm:
  - Policy engine thuần (allow, deny, escalate), IPS có hash, permission tiers T0, T2, T3 và dừng khẩn cấp.
  - Audit log chỉ ghi thêm, có chuỗi hash.
  - Research và chấm điểm rủi ro bằng code (DefiLlama, đọc contract qua RPC).
  - Planner LLM (OpenAI) và Reader LLM cách ly, chống prompt injection.
  - Gateway: đề xuất, policy, mô phỏng, ký. Chỉ gateway được gọi bộ ký.
  - Aave V3 supply và withdraw trên Base Sepolia.
  - Eval harness 3 lớp, báo cáo JSON và HTML.
  - API FastAPI làm contract duy nhất; UI tối thiểu bám contract.
  - Tài liệu nộp: kiến trúc, bảo mật, tự đánh giá, bằng chứng tx hash, deck, video.
- Không làm (hoặc chỉ khi lõi xong, theo thứ tự cắt):
  1. Bonus ép luật on-chain (ERC-7715, Safe session keys).
  2. UI đẹp (Claude Design hoặc AI Studio); giữ UI tối thiểu.
  3. Ví người dùng ký (T1): chỉ mô tả trong tài liệu.
  4. CDP Server Wallet: dùng ví thường, khóa trong `.env`, chỉ testnet.
  5. Reader LLM: thay bằng kiểm tra allowlist thuần.
  - Uniswap swap: bỏ nếu pool testnet không có thanh khoản.
- Không bao giờ cắt: test của policy engine, bộ eval, tx hash, báo cáo tự đánh giá bảo mật.

## Các mốc
Hôm nay là 14/10; còn 6 ngày. Các mốc là mốc tự kiểm của nhóm, đề chỉ có một hạn duy nhất.

| Mốc | Ngày | Xong khi | Issue |
| :--- | :--- | :--- | :--- |
| M0 Tx hash đầu | 15/10 | Một lệnh Aave supply đi qua gateway, có tx hash trên BaseScan Sepolia | chưa tạo |
| M1 Lõi an toàn | 17/10 | Policy engine và test lớp A đạt 100%; audit log có chuỗi hash, phát hiện sửa dòng | chưa tạo |
| M2 Lõi chạy trọn | 18/10 | Research và chấm rủi ro, Planner, gateway, eval harness sinh báo cáo; 4 nhánh quyết định đều có test | chưa tạo |
| M3 Hồ sơ | 19/10 | UI tối thiểu, README, hướng dẫn deploy, tài liệu kiến trúc và bảo mật, bằng chứng tx hash | chưa tạo |
| M4 Nộp | 20/10 | Deck, video, repo đã chuyển public, 6 mục đã nộp; để dư thời gian cập nhật trước hạn | chưa tạo |

## Rủi ro và câu hỏi mở
- Đội chỉ có một người, đề yêu cầu 2 đến 5 người và ít nhất 60% không mang quốc tịch Trung Quốc. Chặn việc đăng ký → việc của người dùng, xử lý ngay.
- Repo đang private; phải đổi sang public trước hạn nộp và rà secret trong lịch sử commit.
- RPC Base Sepolia có hỗ trợ `eth_simulateV1` không; nếu không thì dùng `eth_call` hoặc fork bằng anvil → ticket research.
- Pool Uniswap trên Base Sepolia có thanh khoản không → ticket research.
- Chưa có địa chỉ Aave V3 Pool trên Base Sepolia xác minh từ tài liệu gốc → ticket research.
- Nguồn về thị trường ví agent phần lớn là thứ cấp; xác minh từ tài liệu gốc trước khi trích vào hồ sơ.
- Điều khoản đầy đủ nằm ở Competition Rules trên fintechathon.g-ican.com; chưa đọc → người dùng.
