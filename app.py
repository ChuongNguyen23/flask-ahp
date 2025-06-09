from psycopg2.extras import RealDictCursor
import os
from flask import Flask, render_template, request, redirect, url_for, flash, session
import json
import numpy as np
import psycopg2
from psycopg2 import sql
import pandas as pd
from io import BytesIO
from flask_mail import Mail, Message
from flask import send_file
from weasyprint import HTML
from flask import jsonify
import psycopg2.extras
from datetime import datetime
import urllib.parse

app = Flask(__name__)
app.jinja_env.globals.update(enumerate=enumerate)
app.secret_key = 'your_secret_key'  # Thay bằng secret key thật của bạn

# -------------------------------
# DATABASE: Kết nối đến PostgreSQL
# -------------------------------
def get_db_connection():
    url = 'postgresql://ahp_db:cfhWQAw8lQC8H8kYPhoPBikl1ZN2MQfr@dpg-d13di4p5pdvs73dl3fig-a/ahp_db_pqhn'
    result = urllib.parse.urlparse(url)

    conn = psycopg2.connect(
        dbname=result.path[1:],
        user=result.username,
        password=result.password,
        host=result.hostname,
        port=result.port,
        sslmode='require'
    )

    # SET SCHEMA mặc định là public để không phải viết public.xe
    cur = conn.cursor()
    cur.execute('SET search_path TO public;')
    cur.close()

    return conn

# -------------------------------
# Truy vấn cấu hình tiêu chí từ bảng criteria_config
# -------------------------------
def get_criteria_config():
    """Truy vấn bảng criteria_config và trả về dictionary cấu hình."""
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT key, display, table_name, field_name, is_cost FROM criteria_config ORDER BY id;")
        rows = cur.fetchall()
        conn.close()
        config = { row[0]: {'display': row[1], 'table': row[2], 'field': row[3], 'is_cost': row[4]} for row in rows }
        return config
    except Exception as e:
        flash("Lỗi truy xuất dữ liệu cấu hình tiêu chí: " + str(e))
        return {}
    
# --- chèn ngay dưới hàm get_criteria_config() ---
def get_criteria_options():
    """
    Trả về list dict mỗi phần tử có:
      - value: khóa dùng trong form
      - display: tên hiển thị
      - mo_ta: mô tả
    """
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        # Join bảng cấu hình với bảng tiêu chí để lấy mô tả
        cur.execute("""
            SELECT cfg.key, cfg.display, tc.mo_ta
            FROM criteria_config AS cfg
            JOIN tieu_chi AS tc
              ON cfg.key = tc.ten
            ORDER BY cfg.id;
        """)
        rows = cur.fetchall()
        conn.close()
        return [
            {'value': key, 'display': display, 'mo_ta': mo_ta}
            for key, display, mo_ta in rows
        ]
    except Exception as e:
        flash("Lỗi khi lấy danh sách tiêu chí: " + str(e))
        return []
# -------------------------------
# Hàm tính AHP: trả về vector trọng số, lambda_max, CI và CR.
# -------------------------------
def compute_ahp(matrix):
    matrix = np.array(matrix, dtype=float)
    col_sum = matrix.sum(axis=0)
    norm_matrix = matrix / col_sum
    w = norm_matrix.mean(axis=1)
    
    weighted_sum = np.dot(matrix, w)
    lambda_max = (weighted_sum / w).mean()
    n = len(w)
    CI = (lambda_max - n) / (n - 1) if n > 1 else 0
    RI_dict = {1: 0, 2: 0, 3: 0.58, 4: 0.90, 5: 1.12, 6: 1.24, 7: 1.32}
    RI = RI_dict.get(n, 1.32)
    CR = CI / RI if RI != 0 else 0
    return w, lambda_max, CI, CR

def validate_value(val):
    try:
        v = float(val)
        # Chỉ chấp nhận v trong khoảng [1/9, 9]
        return 1/9 <= v <= 9
    except Exception:
        return False


def save_calculation_history(vehicles, criteria, crit_weights, results, matrices):
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        query = """
            INSERT INTO calculation_history (vehicles, criteria, crit_weights, results, matrices)
            VALUES (%s, %s, %s, %s, %s);
        """
        cur.execute(query, (
            json.dumps(vehicles),
            json.dumps(criteria),
            json.dumps(crit_weights),
            json.dumps(results),
            json.dumps(matrices)
        ))
        conn.commit()
        conn.close()
    except Exception as e:
        flash("Lỗi lưu lịch sử tính toán: " + str(e))

# -------------------------------
# ROUTE: Trang chủ -> chuyển hướng sang lọc public.xe
# -------------------------------
@app.route('/')
def index():
    return redirect(url_for('filter_vehicles'))

# -------------------------------
# ROUTE: Lọc public.xe theo loại public.xe, phân khúc và loại năng lượng
# -------------------------------
@app.route('/filter', methods=['GET', 'POST'])
def filter_vehicles():
    if request.method == 'POST':
        selected_segments = request.form.getlist('segment')
        selected_energies = request.form.getlist('energy')
        selected_loai = request.form.getlist('loai_xe')

        if not selected_segments and not selected_loai:
            flash("Vui lòng chọn ít nhất 1 phân khúc hoặc 1 loại public.xe.")
            return redirect(url_for('filter_vehicles'))
        if not selected_energies:
            flash("Vui lòng chọn ít nhất 1 loại năng lượng.")
            return redirect(url_for('filter_vehicles'))

        if selected_loai:
            session['selected_loai'] = selected_loai
        session['segments'] = selected_segments
        session['energies'] = selected_energies

        try:
            conn = get_db_connection()
            cur = conn.cursor()
            if session.get('selected_loai'):
                cur.execute("""
                    SELECT ten_xe, img_path, model_year, mileage, hp, transmission, price, description
                    FROM xe
                    WHERE loai_xe = ANY(%s) AND loai_nl = ANY(%s)
                    ORDER BY ten_xe;
                """, (session['selected_loai'], session['energies']))
            else:
                cur.execute("""
                    SELECT ten_xe, img_path, model_year, mileage, hp, transmission, price, description
                    FROM xe
                    WHERE phan_khuc = ANY(%s) AND loai_nl = ANY(%s)
                    ORDER BY ten_xe;
                """, (session['segments'], session['energies']))
            rows = cur.fetchall()
            conn.close()
        except Exception as e:
            flash("Lỗi truy xuất dữ liệu public.xe: " + str(e))
            return redirect(url_for('filter_vehicles'))

        if not rows:
            flash("Không có public.xe nào thỏa mãn điều kiện lọc.")
            return redirect(url_for('filter_vehicles'))

        vehicles = [
            {
                'ten_xe': row[0],
                'img_path': row[1],
                'model_year': row[2],
                'mileage': row[3],
                'hp': row[4],
                'transmission': row[5],
                'price': row[6],
                'description': row[7]
            }
            for row in rows
        ]
        session['vehicles'] = [v['ten_xe'] for v in vehicles]
        return render_template('select_vehicles.html', vehicles=vehicles)

    else:
        try:
            conn = get_db_connection()
            cur = conn.cursor()
            cur.execute("SELECT DISTINCT loai_xe FROM xe ORDER BY loai_xe;")
            loai_xe_list = [row[0] for row in cur.fetchall()]
            cur.execute("SELECT DISTINCT phan_khuc, loai_xe FROM phan_khuc_loai_xe;")
            rows = cur.fetchall()
            segments = list(set([row[0] for row in rows]))
            segment_loai = {row[0]: row[1] for row in rows}
            cur.execute("SELECT DISTINCT loai_nl FROM xe;")
            energies = [row[0] for row in cur.fetchall()]
            conn.close()
        except Exception as e:
            flash("Lỗi truy xuất dữ liệu public.xe: " + str(e))
            loai_xe_list, segment_loai, segments, energies = [], {}, [], []

        return render_template(
  'filter.html',
  loai_xe_list=loai_xe_list,
  segments=segments,
  energies=energies,
  segment_loai=segment_loai,
  loai_xe_descriptions={
    'Coupe': 'là dòng public.xe thể thao mang phong cách thanh lịch và quyến rũ, thường được thiết kế với hai cửa, mái che cố định và dáng lưng thấp, tạo đường nét mượt mà từ đầu đến đuôi public.xe. Đặc trưng nhất của coupe là phần nóc public.xe ngắn, cửa sổ hẹp và khoang lái gọn gàng, thường chỉ dành cho 2–4 hành khách. Kiểu dáng này nhấn mạnh vào trải nghiệm lái linh hoạt, động cơ mạnh mẽ và khả năng bám đường ổn định. Một số mẫu coupe hiện đại như BMW 4 Series hay Audi A5 thậm chí có phiên bản bốn cửa nhưng vẫn giữ nguyên tỷ lệ thấp và dáng dốc phía sau. Coupe phù hợp với người yêu tốc độ, ưa phong cách cá tính và không quá đề cao không gian rộng rãi. Tuy hạn chế về khoang hành lý và chỗ ngồi phía sau, coupe lại tỏa sáng ở thiết kế đậm chất nghệ thuật và cảm giác lái phấn khích.',
    'Crossover (CUV)': 'là sự kết hợp giữa SUV và sedan, sở hữu khung gầm public.xe con nhưng được nâng cao để tăng khả năng di chuyển trên nhiều địa hình. Điểm nhận diện rõ nhất là dáng đứng cao, khoang lái rộng và gầm public.xe vừa phải, giúp tầm nhìn lái tốt hơn sedan truyền thống. Crossover như Toyota RAV4 hay Honda CR-V thường có hệ dẫn động cầu trước (FWD) hoặc hai cầu (AWD), cân bằng giữa tiết kiệm nhiên liệu và tính đa dụng. Nội thất được tối ưu cho gia đình với hàng ghế gập linh hoạt, cửa hậu rộng và công nghệ an toàn hiện đại. Khác với SUV cỡ lớn, crossover nhẹ nhàng hơn, phù hợp với đô thị nhưng vẫn đủ sức “xông pha” trên đường trơn trượt hoặc đất đá. Đây là lựa chọn hàng đầu cho những ai cần public.xe đa năng, kết hợp giữa phong cách thể thao và tiện ích đời sống.',
    'Hatchback': 'nổi bật với cửa hậu liền khoang hành lý, mở lên cao để dễ dàng xếp đồ. Thiết kế này gộp cabin và cốp thành một khối thống nhất (kiểu 2-box), tạo nên thân public.xe ngắn gọn, lý tưởng cho việc di chuyển trong thành phố. Xe thường có 3 hoặc 5 cửa, điển hình như Volkswagen Golf hay Hyundai i20. Dù kích thước nhỏ, hatchback tận dụng không gian thông minh: ghế sau gập phẳng giúp chở hàng cồng kềnh, trong khi khoang lái được bố trí hợp lý cho 4–5 người. Hatchback phù hợp với sinh viên, người độc thân hoặc gia đình nhỏ nhờ tính cơ động, dễ đỗ public.xe và tiết kiệm nhiên liệu. Tuy không sang trọng như sedan hay mạnh mẽ như SUV, hatchback vẫn là “chuyên gia đô thị” với giá thành hợp lý và sự tiện lợi tối ưu.',
    'Limousine':'là biểu tượng của xa xỉ và đẳng cấp, thường được thiết kế dài hơn phiên bản sedan tiêu chuẩn để tăng không gian nội thất. Đặc trưng nhất là dãy cửa kính dài, khoang lái riêng biệt cho tài xế và hệ thống giải trí cao cấp như màn hình TV, quầy bar mini trong phiên bản stretch limo. Xe thường được sử dụng cho dịch vụ thuê cao cấp, đám cưới hoặc sự kiện quan trọng. Các mẫu limousine thương mại như Mercedes-Maybach S-Class hay Rolls-Royce Phantom tập trung vào chất liệu sang trọng (da, gỗ tự nhiên), công nghệ cách âm và trải nghiệm êm ái tuyệt đối. Dù kém linh hoạt do kích thước cồng kềnh, limousine vẫn là lựa chọn số một cho những ai muốn khẳng định địa vị và tận hưởng không gian riêng tư tối thượng.',
    'MPV (Multi-Purpose Vehicle)':'MPV, hay còn gọi là public.xe đa dụng, được thiết kế tối ưu cho nhu cầu chở người với không gian nội thất rộng rãi và linh hoạt. Khác SUV, MPV có thân hình vuông vức, trần cao và sàn thấp để tăng diện tích sử dụng, điển hình như Toyota Innova hay Kia Carnival. Xe thường sở hữu 3 hàng ghế, trong đó hàng thứ ba có thể gập hoặc tháo rời, cùng cửa trượt hai bên tiện lợi cho việc lên xuống. MPV phù hợp với gia đình đông thành viên nhờ khả năng chở 7–8 người thoải mái, kèm khoang hành lý đủ rộng dù đã xếp hết ghế. Tuy thiếu phong cách thể thao và khả năng off-road, MPV lại vượt trội ở sự thiết thực, an toàn và tiện nghi cho các chuyến đi dài.',
    'Sedan':'là dòng public.xe phổ biến nhất với thiết kế 3 khoang riêng biệt: động cơ, cabin và cốp sau. Xe có 4 cửa, dáng thấp và cân đối, phù hợp cho gia đình nhỏ hoặc cá nhân ưa sự truyền thống. Điểm mạnh của sedan như Honda Accord hay Toyota Camry là sự êm ái khi di chuyển đường dài, khoang lái yên tĩnh và tiết kiệm nhiên liệu. Cốp public.xe tách biệt giúp cách âm tốt hơn hatchback, đồng thời đảm bảo tính an ninh cho hành lý. Sedan đa dạng từ phân khúc giá rẻ đến cao cấp, cân bằng giữa phong cách và công năng. Dù không đa dụng như SUV hay MPV, sedan vẫn là lựa chọn an toàn nhờ độ tin cậy và chi phí bảo trì hợp lý.',
    'SUV (Sport Utility Vehicle)':'SUV là dòng public.xe đa địa hình, kết hợp sức mạnh của public.xe tải và tiện nghi của public.xe du lịch. Đặc điểm nhận dạng gồm gầm cao, thân public.xe vuông vức và hệ dẫn động AWD/4WD cho khả năng off-road. SUV chia thành hai nhóm: SUV khung liền (unibody) như Ford Escape phù hợp đô thị và SUV khung rời (body-on-frame) như Toyota Land Cruiser dành cho địa hình hiểm trở. Không gian nội thất rộng cùng khoang hành lý lớn là ưu điểm vượt trội, phù hợp cho gia đình hoặc chở hàng hóa. SUV cũng được ưa chuộng nhờ tầm nhìn lái cao, an toàn vượt trội và phong cách thể thao. Dù tiêu hao nhiên liệu hơn sedan, SUV vẫn chiếm lĩnh thị trường nhờ sự đa năng và khả năng thích ứng với mọi nhu cầu.',
    # ...
  },
  segment_descriptions={
    'Phân khúc hạng A (City Car)': 'Hạng A gồm những chiếc public.xe siêu nhỏ, tối ưu cho việc di chuyển trong thành phố. Kích thước chỉ dài khoảng 3–3.6m, động cơ thường dưới 1.2L (như Toyota Aygo, Hyundai i10), giúp tiết kiệm nhiên liệu tối đa. Thiết kế gọn nhẹ, bán kính quay vòng hẹp và giá thành rẻ là ưu điểm nổi bật, phù hợp với sinh viên hoặc người mới lái. Nội thất đơn giản, chỗ ngồi cho 4 người nhưng hạn chế về khoang hành lý. Một số mẫu điện tử như Honda e hay MINI Electric đang dần phổ biến nhằm giảm khí thải. Tuy không phù hợp đường trường dài, public.xe hạng A vẫn là "bậc thầy" đỗ public.xe trong không gian chật hẹp và chi phí vận hành thấp.',
    'Phân khúc hạng B (Supermini)': 'Hạng B là public.xe hatchback cỡ nhỏ, lớn hơn hạng A một chút (dài 3.8–4.2m), phục vụ người dùng cần public.xe tiết kiệm nhưng không quá chật chội. Động cơ phổ biến từ 1.0L–1.6L (như Ford Fiesta, Peugeot 208), cân bằng giữa hiệu suất và mức tiêu thụ nhiên liệu. Thiết kế trẻ trung, nội thất tối ưu hóa không gian với ghế sau gập 60/40 và khoang chứa đồ khoảng 300–350 lít. Công nghệ tập trung vào giải trí và an toàn cơ bản. Đây là lựa chọn lý tưởng cho người độc thân hoặc cặp đôi trẻ, ưa thích sự năng động và dễ dàng di chuyển trong đô thị đông đúc.',
    'Phân khúc hạng C (Compact Car)': 'Hạng C là phân khúc public.xe gia đình cỡ trung, cân bằng giữa kích thước và giá cả. Dài khoảng 4.2–4.5m, động cơ từ 1.4L–2.0L (như Volkswagen Golf, Honda Civic), đáp ứng nhu cầu di chuyển hàng ngày lẫn chuyến đi xa. Thiết kế đa dạng: hatchback, sedan hoặc wagon, với không gian cho 5 người thoải mái và cốp public.xe từ 380–500 lít. Công nghệ tiêu chuẩn gồm màn hình cảm ứng, kết nối Apple CarPlay và hệ thống an toàn cơ bản. Đây là phân khúc cạnh tranh gay gắt, thu hút người dùng trẻ tuổi hoặc gia đình nhỏ nhờ độ tin cậy cao, chi phí bảo trì hợp lý và tính linh hoạt trong sử dụng.',
    'Phân khúc hạng D (Large Family Car)': 'Hạng D là public.xe gia đình cỡ lớn, dài 4.6–4.9m (như Toyota Camry, Skoda Superb), kết hợp không gian rộng rãi và công nghệ hiện đại. Động cơ từ 1.8L–2.5L, cung cấp đủ sức mạnh cho hành trình dài. Nội thất tiện nghi với ghế ngả sâu, sưởi ghế và khoang hành lý trên 500 lít. Thiết kế nghiêng về sự thoải mái, hệ thống treo êm ái và cabin cách âm tốt. Xe hạng D phù hợp cho gia đình cần public.xe an toàn, bền bỉ và đủ sang trọng để sử dụng trong nhiều năm. Dù cạnh tranh với SUV, chúng vẫn giữ chỗ đứng nhờ chi phí vận hành thấp hơn và cảm giác lái ổn định.',
    'Phân khúc hạng E (Executive Car)': 'Hạng E dành cho dòng public.xe hạng sang cỡ trung, phục vụ doanh nhân hoặc người có thu nhập cao. Kích thước dài 4.8–5m (như BMW 5 Series, Mercedes E-Class), sở hữu động cơ mạnh mẽ (từ 2.0L đến V6) và công nghệ cao cấp: ghế massage, màn hình head-up, hệ thống âm thanh cao cấp. Nội thất sang trọng với chất liệu da tự nhiên, gỗ veneer và khả năng cách âm vượt trội. Không gian hàng ghế sau rộng rãi, phù hợp để đón tiếp đối tác. Xe hạng E thường được dùng làm public.xe công ty hoặc phương tiện cá nhân cho những ai coi trọng đẳng cấp nhưng không muốn quá phô trương như hạng F.',
    'Phân khúc hạng F (Luxury Car)': 'Hạng F đại diện cho đỉnh cao của public.xe sang trọng, thường là sedan hoặc limousine dài trên 5.2m (như Mercedes-Maybach S-Class, Rolls-Royce Ghost). Động cơ khủng (V8, V12 hoặc hybrid), nội thất dát vàng, sử dụng da cao cấp và gỗ quý. Công nghệ đột phá như hệ thống lọc không khí, màn hình giải trí riêng biệt cho từng ghế và khả năng tự lái cấp độ cao. Không gian tập trung vào hàng ghế sau với chế độ thư giãn toàn diện. Xe hạng F thường do tài xế lái, phục vụ giới siêu giàu hoặc nguyên thủ quốc gia. Giá thành có thể lên tới hàng triệu USD, đi kèm dịch vụ bảo dưỡng đặc quyền.',
    'Phân khúc hạng J (SUV/Crossover)': 'Phân khúc hạng J là nhóm public.xe SUV (Sport Utility Vehicle) và Crossover, kết hợp giữa khả năng off-road của public.xe địa hình và tiện nghi của public.xe gia đình. Đặc trưng bởi gầm cao, thân public.xe vuông vức, hệ dẫn động hai cầu (AWD/4WD), cùng không gian nội thất rộng rãi. Xe hạng J thường chia thành hai nhóm: SUV cỡ nhỏ (như Honda CR-V, Toyota RAV4) phù hợp đô thị và SUV cỡ lớn (như Ford Explorer, Land Rover Defender) cho địa hình phức tạp. Thiết kế chú trọng vào tính đa dụng: khoang hành lý lớn, hệ thống treo êm ái, và công nghệ an toàn như kiểm soát hành trình hay cảnh báo điểm mù. Đối tượng hướng đến là gia đình cần không gian linh hoạt, người yêu du lịch hoặc những ai ưa phong cách thể thao. Dù tiêu hao nhiên liệu cao hơn sedan, hạng J vẫn thống trị thị trường nhờ khả năng thích ứng với mọi nhu cầu, từ di chuyển nội đô đến khám phá xa.',
    'Phân khúc hạng M (MPV/Minivan)': 'Hạng M là public.xe đa dụng (MPV), tập trung vào không gian chở người với 3 hàng ghế (như Toyota Alphard, Kia Carnival). Thiết kế vuông vức, trần cao và cửa trượt hai bên giúp lên xuống dễ dàng. Động cơ thường từ 2.0L–3.5L, cân bằng giữa sức mạnh và tải trọng. Nội thất linh hoạt: ghế gập, bàn xoay hoặc khu vực giải trí tích hợp. Xe hạng M phù hợp gia đình đông thành viên, doanh nghiệp cần đón tiếp khách hoặc dịch vụ taxi cao cấp. Dù kém hấp dẫn về thiết kế, chúng vẫn không có đối thủ về tiện ích và khả năng chuyên chở.',
    'Phân khúc hạng S (Sports Car)': 'Hạng S là thế giới của public.xe thể thao hiệu suất cao, tập trung vào tốc độ và trải nghiệm lái. Thiết kế thấp, động cơ mạnh (từ turbocharged đến V12), hệ thống treo thể thao và khí động học tối ưu (như Porsche 911, Chevrolet Corvette). Nội thất đơn giản hóa để giảm trọng lượng, vật liệu sử dụng carbon fiber hoặc Alcantara. Đa phần là public.xe hai cửa, chỗ ngồi cho 2–4 người nhưng không gian hạn chế. Hạng S hướng đến người đam mê tốc độ, sẵn sàng chi trả hàng trăm nghìn USD cho công nghệ đua public.xe hoặc phiên bản giới hạn. Dù kém tiện nghi và khó sử dụng hàng ngày, chúng vẫn là biểu tượng của đam mê và kỹ thuật cơ khí đỉnh cao.',
    # ...
  },
  energy_descriptions={
    'Xăng': 'Xe chạy bằng xăng là loại phương tiện truyền thống, hoạt động nhờ động cơ đốt trong sử dụng nhiên liệu xăng. Ưu điểm lớn của loại public.xe này là dễ tiếp nhiên liệu, phù hợp với hạ tầng hiện có, và khả năng vận hành ổn định trên đường dài. Thời gian tiếp nhiên liệu nhanh, chỉ mất vài phút là public.xe có thể tiếp tục di chuyển. Tuy nhiên, public.xe xăng tiêu thụ nhiên liệu hóa thạch nên phát thải khí CO₂ nhiều hơn, ảnh hưởng đến môi trường. Đây là lựa chọn phù hợp với những ai cần sự tiện lợi và quen thuộc.',
    'Điện': 'Xe điện hoạt động hoàn toàn bằng động cơ điện, sử dụng pin sạc thay cho nhiên liệu truyền thống. Ưu điểm lớn nhất là không phát thải khí ô nhiễm khi vận hành, giúp bảo vệ môi trường và tiết kiệm chi phí nhiên liệu. Xe chạy rất êm ái, ít rung động, và chi phí bảo trì thường thấp hơn do ít bộ phận cơ khí hơn. Tuy nhiên, public.xe điện phụ thuộc vào hệ thống sạc điện, thời gian sạc lâu hơn so với tiếp nhiên liệu xăng, và phạm vi di chuyển còn hạn chế ở một số mẫu public.xe. Đây là lựa chọn hiện đại, thân thiện với môi trường, phù hợp cho người sống ở đô thị.',
    'Hybrid': 'Xe hybrid kết hợp cả động cơ xăng và động cơ điện, giúp tối ưu hóa khả năng tiết kiệm nhiên liệu và giảm khí thải. Trong điều kiện vận hành bình thường, public.xe có thể tự chuyển đổi giữa hai loại năng lượng để đạt hiệu suất cao nhất. Khi di chuyển chậm hoặc kẹt public.xe, public.xe dùng động cơ điện để tiết kiệm xăng; khi cần sức mạnh, động cơ xăng sẽ hỗ trợ. Ưu điểm của public.xe hybrid là tiết kiệm nhiên liệu, giảm phát thải và không cần sạc điện từ bên ngoài. Đây là giải pháp trung gian lý tưởng cho khách hàng muốn chuyển dần sang public.xe điện mà vẫn đảm bảo sự linh hoạt.',
    # ...
  }
)

# -------------------------------
# ROUTE: Nếu khách hàng không muốn lọc theo các tiêu chí trên, chọn tất cả public.xe
# -------------------------------
@app.route('/select_all_vehicles')
def select_all_vehicles():
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("""
            SELECT ten_xe, img_path, model_year, mileage, hp, transmission, price, description
            FROM xe
            ORDER BY ten_xe;
        """)
        rows = cur.fetchall()
        conn.close()
        vehicles = [
            {
                'ten_xe': row[0],
                'img_path': row[1],
                'model_year': row[2],
                'mileage': row[3],
                'hp': row[4],
                'transmission': row[5],
                'price': row[6],
                'description': row[7]
            }
            for row in rows
        ]
        session['vehicles'] = [v['ten_xe'] for v in vehicles]
        return render_template('select_vehicles.html', vehicles=vehicles)
    except Exception as e:
        flash("Lỗi truy xuất dữ liệu public.xe: " + str(e))
        return redirect(url_for('filter_vehicles'))


# -------------------------------
# ROUTE: Trang chọn public.xe từ danh sách lọc
# -------------------------------
@app.route('/select_vehicles', methods=['POST'])
def select_vehicles():
    selected_vehicles = request.form.getlist('vehicle')
    if not selected_vehicles:
        flash("Vui lòng chọn ít nhất 2 public.xe.")
        return redirect(url_for('filter_vehicles'))
    session['selected_vehicles']   = selected_vehicles
    session['alternative_names']   = selected_vehicles   # ← thêm dòng này
    flash("Danh sách public.xe đã được lưu. Tiếp theo, hãy chọn các tiêu chí (từ 2 đến 7).")
    return redirect(url_for('select_criteria_page'))

# -------------------------------
# ROUTE: Trang chọn tiêu chí
# -------------------------------
# -------------------------------
# Map từ crit.value → class flaticon (cấp module)
# -------------------------------
# app.py
# ở đầu app.py, module–level
ICON_MAP = {
    'an_toan'    : 'fas fa-shield-alt',    # shield
    'chi_phi'    : 'fas fa-dollar-sign',   # dollar
    'cong_nghe'  : 'fas fa-microchip',     # microchip
    'hieu_suat'  : 'fas fa-tachometer-alt',# tachometer
    'khau_hao'   : 'fas fa-chart-line',    # chart-line
    'thuong_hieu': 'fas fa-star'           # star
}



# -------------------------------
# ROUTE: Trang chọn tiêu chí
# -------------------------------
@app.route('/select_criteria', methods=['GET'])
def select_criteria_page():
    criteria_options = get_criteria_options()
    return render_template(
        'select_criteria.html',
        criteria_options=criteria_options,
        icon_map=ICON_MAP
    )



# -------------------------------
# ROUTE: Lưu tiêu chí đã chọn
# -------------------------------
@app.route('/criteria', methods=['POST'])
def save_criteria():
    selected = request.form.getlist('criteria')
    if len(selected) < 2 or len(selected) > 7:
        flash("Vui lòng chọn từ 2 đến 7 tiêu chí.")
        return redirect(url_for('select_criteria_page'))
        # Lấy cấu hình tiêu chí
    full_config = get_criteria_config()

    # Ánh xạ key → display
    crits_vn = [ full_config[c]['display'] for c in selected ]

    # Lưu vào session để sau này còn dùng (ví dụ chart hoặc result)
    session['selected_criteria']    = selected
    session['selected_criteria_vn'] = crits_vn

    # Trả về template, truyền thêm crits_vn
    return render_template(
      'criteria.html',
      crits       = selected,   # còn để lấy tên ô input cell_i_j
      crits_vn    = crits_vn     # dùng cho hiển thị tiếng Việt
    )


# -------------------------------
# ROUTE: Nhập ma trận so sánh cặp (ban đầu) để tính trọng số tiêu chí
# -------------------------------
@app.route('/criteria_matrix', methods=['POST'])
def criteria_matrix():
    selected = session.get('selected_criteria', None)
    if not selected or len(selected) < 2 or len(selected) > 7:
        flash("Vui lòng chọn từ 2 đến 7 tiêu chí.")
        return redirect(url_for('select_criteria_page'))
    try:
        n = len(selected)
        matrix = [[1 if i == j else 0 for j in range(n)] for i in range(n)]
        for i in range(n):
            for j in range(i + 1, n):
                key = f"cell_{i}_{j}"
                raw_val = request.form.get(key)
                if raw_val is None or not validate_value(raw_val):
                    flash(f"Giá trị ở ô ({i+1},{j+1}) không hợp lệ hoặc bị bỏ trống.")
                    return redirect(url_for('save_criteria'))
                val = float(raw_val)
                matrix[i][j] = val
                matrix[j][i] = 1 / val
    except Exception as e:
        flash("Lỗi nhập liệu cho ma trận tiêu chí: " + str(e))
        return redirect(url_for('save_criteria'))
    
    w, lambda_max, CI, CR = compute_ahp(matrix)
    if CR >= 0.1:
        flash(f"Ma trận tiêu chí không nhất quán (CR = {CR:.3f} ≥ 0.1). Vui lòng điều chỉnh lại.")
        return redirect(url_for('save_criteria'))

    # Lưu vào session
    session['crit_weights'] = w.tolist()
    session['criteria_consistency'] = {'lambda_max': lambda_max, 'CI': CI, 'CR': CR}

    flash("✅ Trọng số tiêu chí đã được tính thành công.")

    # Hiển thị lại criteria.html để trực quan hóa
    return redirect(url_for('result'))


# -------------------------------
# ROUTE: Nhận ma trận con do user nhập, tính AHP tổng và render kết quả
# -------------------------------
@app.route('/custom_matrix', methods=['POST'])
def custom_matrix():
    selected = session.get('selected_criteria')
    crit_weights = session.get('crit_weights')
    alternatives = session.get('alternative_names')
    full_cfg = get_criteria_config()

    # Build lại sub_vectors từ form
    sub_vectors = {}
    for crit in selected:
        info = full_cfg[crit]
        n = len(alternatives)
        M = [[1.0]*n for _ in range(n)]
        for i in range(n):
            for j in range(i+1, n):
                raw = request.form.get(f"matrix_{crit}_{i}_{j}")
                if raw is None or not validate_value(raw):
                    flash(f"Giá trị ô ({i+1},{j+1}) của '{info['display']}' không hợp lệ.")
                    return render_template('matrix_display.html',
                                           matrices=session.get('matrices_detail'),
                                           alternatives=alternatives)
                v = float(raw)
                M[i][j], M[j][i] = v, 1.0/v
        w, _, _, cr = compute_ahp(M)
        if cr >= 0.1:
            flash(f"Ma trận '{info['display']}' không nhất quán (CR={cr:.3f}).")
            return render_template('matrix_display.html',
                                   matrices=session.get('matrices_detail'),
                                   alternatives=alternatives)
        sub_vectors[crit] = {'original': M, 'weights': w.tolist()}

    # Lưu session mới
    session['matrices_detail'] = sub_vectors

    # Thay vì redirect, render lại matrix_display
    return redirect(url_for('matrix_display'))


# -------------------------------
# ROUTE: Tính kết quả AHP
# -------------------------------
@app.route('/result')
def result():
    selected = session.get('selected_criteria', None)
    if not selected or not (2 <= len(selected) <= 7):
        flash("Chưa chọn số lượng tiêu chí hợp lệ.")
        return redirect(url_for('select_criteria_page'))
    crit_weights = session.get('crit_weights', None)
    if not crit_weights:
        flash("Chưa có trọng số tiêu chí, vui lòng nhập lại ma trận tiêu chí.")
        return redirect(url_for('save_criteria'))
    
    full_criteria = get_criteria_config()
    selected_info = {crit: full_criteria[crit] for crit in selected if crit in full_criteria}
    chosen_vehicles = session.get('selected_vehicles', None)
    if not chosen_vehicles:
        flash("Chưa có danh sách public.xe đã chọn.")
        return redirect(url_for('select_vehicles'))
    
    sub_vectors = {}
    alternative_names = None
    for crit, info in selected_info.items():
        try:
            conn = get_db_connection()
            cur = conn.cursor()
            query = sql.SQL("SELECT ten_xe, {field} FROM {table} WHERE ten_xe = ANY(%s) ORDER BY ten_xe;").format(
                field=sql.Identifier(info['field']),
                table=sql.Identifier(info['table'])
            )
            cur.execute(query, (chosen_vehicles,))
            rows = cur.fetchall()
            conn.close()
        except Exception as e:
            flash(f"Lỗi truy xuất dữ liệu từ bảng {info['table']}: " + str(e))
            return redirect(url_for('select_criteria_page'))
        if not rows:
            flash(f"Bảng {info['table']} không có dữ liệu cho các public.xe đã chọn.")
            return redirect(url_for('select_criteria_page'))
        names = [row[0] for row in rows]
        if alternative_names is None:
            alternative_names = names
        else:
            if alternative_names != names:
                flash("Trật tự public.xe giữa các bảng không đồng nhất.")
                return redirect(url_for('select_criteria_page'))
        values = []
        for row in rows:
            try:
                values.append(float(row[1]))
            except Exception:
                flash(f"Lỗi chuyển đổi giá trị trong bảng {info['table']} cho public.xe {row[0]}.")
                return redirect(url_for('select_criteria_page'))
         # Build pairwise matrix with clamped ratios
        n = len(values)
        matrix = [[1.0] * n for _ in range(n)]
        for i in range(n):
            for j in range(i + 1, n):
                if info.get('is_cost'):
                    # cost criterion: lower is better
                    ratio = values[i] / values[j] if values[j] != 0 else 9.0
                else:
                    # benefit criterion
                    ratio = values[j] / values[i] if values[i] != 0 else 9.0

                # --- BỔ SUNG CLAMP TỈ LỆ TRONG KHOẢNG [1/9, 9] ---
                ratio = max(min(ratio, 9.0), 1/9)
                # ---------------------------------------------------

                matrix[i][j] = ratio
                matrix[j][i] = 1.0 / ratio
        local_w, local_lambda_max, local_CI, local_CR = compute_ahp(matrix)
        if local_CR >= 0.1:
            flash(f"Ma trận cho tiêu chí {info['display']} không nhất quán (CR = {local_CR:.3f} ≥ 0.1).")
            return redirect(url_for('select_criteria_page'))
        sub_vectors[crit] = {
            'original':   matrix,
            'normalized': (np.array(matrix) / np.array(matrix).sum(axis=0)).tolist(),
            'weights':    local_w.tolist(),
            'lambda_max': local_lambda_max,
            'ci':         local_CI,
            'cr':         local_CR
        }

    results = []
    m_alt = len(alternative_names)
    for i in range(m_alt):
        score = 0.0
        for idx, crit in enumerate(selected):
            if crit in sub_vectors:
                score += crit_weights[idx] * sub_vectors[crit]['weights'][i]
        results.append({'name': alternative_names[i], 'score': score})
    total = sum(item['score'] for item in results)
    if total:
        for item in results:
            item['score'] /= total
    results = sorted(results, key=lambda x: x['score'], reverse=True)
    # XÓA ma trận cũ
    session.pop('matrices_detail', None)
    session.pop('alternative_names', None)
    # LƯU ma trận mới 
    session['matrices_detail'] = sub_vectors
    session['alternative_names'] = alternative_names
    save_calculation_history(chosen_vehicles, selected, crit_weights, results, sub_vectors)
    
    consistency = session.get('criteria_consistency', {'lambda_max': None, 'CI': None, 'CR': None})
    
    # Chuẩn bị data cho chart theo đúng thứ tự bảng
    alt_labels = [r['name']  for r in results]
    alt_scores = [r['score'] for r in results]
    crit_labels = selected
    crit_values = crit_weights

    # Lấy tên hiển thị cho các tiêu chí đã chọn
    crit_labels_vn = [ full_criteria[crit]['display'] for crit in selected ]
    # Sau khi tính xong results, alt_labels, alt_scores, crit_values, crit_labels_vn
    session['results']        = results
    session['alt_labels']     = alt_labels
    session['alt_scores']     = alt_scores
    session['crit_values']    = crit_values
    session['crit_labels_vn'] = crit_labels_vn

    return render_template('result.html', results=results, crit_weights=crit_weights, 
                           lambda_max=consistency['lambda_max'], ci=consistency['CI'], cr=consistency['CR'], 
                           # Truyền thêm JSON strings
        # Pass list trực tiếp — không json.dumps
    alt_labels=alt_labels,
    alt_scores=alt_scores,
    crit_labels=crit_labels,
    crit_values=crit_values,
    crit_labels_vn=crit_labels_vn
)

#Xuất Excel và PDF

def compute_ahp_steps(matrix):
    """
    Nhận ma trận AHP (list of lists hoặc numpy array),
    trả về dict chứa:
      - mat_orig: numpy array gốc
      - col_sum: numpy array tổng mỗi cột
      - mat_norm: numpy array ma trận chuẩn hóa
      - weights: numpy array vector trọng số
      - weighted_sum: numpy array tích có trọng số (A·w)
      - lambdas: numpy array λ_i = weighted_sum_i / w_i
      - lambda_max: float trung bình λ_i
      - CI, CR: floats
    """
    A = np.array(matrix, dtype=float)
    n = A.shape[0]

    # 1) Tổng mỗi cột
    col_sum = A.sum(axis=0)

    # 2) Ma trận chuẩn hóa
    mat_norm = A / col_sum

    # 3) Vector trọng số
    weights = mat_norm.mean(axis=1)

    # 4) Vector tích có trọng số
    weighted_sum = A.dot(weights)

    # 5) Vector λ_i
    lambdas = weighted_sum / weights

    # 6) λ_max = trung bình λ_i
    lambda_max = lambdas.mean()

    # 7) CI, CR
    CI = (lambda_max - n) / (n - 1) if n > 1 else 0
    RI_dict = {1: 0, 2: 0, 3: 0.58, 4: 0.90, 5: 1.12, 6: 1.24, 7: 1.32}
    RI = RI_dict.get(n, 1.32)
    CR = CI / RI if RI != 0 else 0

    return {
        'mat_orig': A,
        'col_sum': col_sum,
        'mat_norm': mat_norm,
        'weights': weights,
        'weighted_sum': weighted_sum,
        'lambdas': lambdas,
        'lambda_max': lambda_max,
        'CI': CI,
        'CR': CR
    }


def create_excel_bytes():
    """
    Xuất Excel gồm:
      1) Sheet 'Kết quả AHP' (kết quả + trọng số tiêu chí)
      2) Với mỗi crit, sheet 'ChiTiet_<crit>' chứa:
         - Ma trận gốc
         - Tổng mỗi cột
         - Ma trận chuẩn hóa
         - Vector trọng số
         - Vector weighted_sum
         - Vector λ_i
         - Dòng tóm tắt λ_max, CI, CR
    """
    # Lấy dữ liệu chung từ session
    results         = session.get('results', [])
    crit_labels_vn  = session.get('crit_labels_vn', [])
    crit_weights    = session.get('crit_weights', [])
    matrices_detail = session.get('matrices_detail', {})
    alternatives    = session.get('alternative_names', [])

    # Chuẩn bị df_res và df_w như trước
    df_res = pd.DataFrame(results)
    if 'score' in df_res.columns:
        df_res['score (%)'] = df_res['score'] * 100

    df_w = pd.DataFrame({
        'Tiêu chí': crit_labels_vn,
        'Trọng số': crit_weights
    })

    buf = BytesIO()
    with pd.ExcelWriter(buf, engine='xlsxwriter') as writer:
        # --- Sheet "Kết quả AHP" ---
        sheet_all = 'Kết quả AHP'
        df_res.to_excel(writer, index=False, sheet_name=sheet_all, startrow=0)
        res_rows = len(df_res) + 1
        df_w.to_excel(writer, index=False, sheet_name=sheet_all, startrow=res_rows + 1)

        # --- Với mỗi crit, tạo sheet ChiTiet_<crit> ---
        for crit, info in matrices_detail.items():
            steps = compute_ahp_steps(info.get('original', []))
            A = steps['mat_orig']
            col_sum = steps['col_sum']
            mat_norm = steps['mat_norm']
            weights = steps['weights']
            weighted_sum = steps['weighted_sum']
            lambdas = steps['lambdas']
            lambda_max = steps['lambda_max']
            CI = steps['CI']
            CR = steps['CR']

            sheet_name = f"ChiTiet_{crit}"
            # 1) Ghi ma trận gốc
            df_mat = pd.DataFrame(A, index=alternatives, columns=alternatives)
            df_mat.to_excel(writer, sheet_name=sheet_name, startrow=0)
            mat_rows = len(df_mat) + 1  # tính số row đã dùng (kể cả header)

            # 2) Ghi tổng mỗi cột, đặt thành một DataFrame 1 hàng
            df_colsum = pd.DataFrame([col_sum], columns=alternatives)
            df_colsum.index = ['Tổng cột']
            df_colsum.to_excel(writer, sheet_name=sheet_name, startrow=mat_rows + 1)
            colsum_rows = 1 + 1  # 1 row dữ liệu + 1 header

            # 3) Ghi ma trận chuẩn hóa, bắt đầu từ row = mat_rows + colsum_rows + 2
            start_norm = mat_rows + colsum_rows + 2
            df_norm = pd.DataFrame(mat_norm, index=alternatives, columns=alternatives)
            df_norm.to_excel(writer, sheet_name=sheet_name, startrow=start_norm)
            norm_rows = len(df_norm) + 1

            # 4) Ghi vector trọng số, dạng DataFrame một cột
            start_w = start_norm + norm_rows + 1
            df_wlocal = pd.DataFrame({
                'Phương án': alternatives,
                'W_local': weights
            })
            df_wlocal.to_excel(writer, sheet_name=sheet_name, index=False, startrow=start_w)
            wlocal_rows = len(df_wlocal) + 1

            # 5) Ghi vector weighted_sum
            start_ws = start_w + wlocal_rows + 1
            df_ws = pd.DataFrame({
                'Phương án': alternatives,
                'WeightedSum': weighted_sum
            })
            df_ws.to_excel(writer, sheet_name=sheet_name, index=False, startrow=start_ws)
            ws_rows = len(df_ws) + 1

            # 6) Ghi vector λ_i
            start_lam = start_ws + ws_rows + 1
            df_lam = pd.DataFrame({
                'Phương án': alternatives,
                'Lambda_i': lambdas
            })
            df_lam.to_excel(writer, sheet_name=sheet_name, index=False, startrow=start_lam)
            lam_rows = len(df_lam) + 1

            # 7) Ghi tóm tắt λ_max, CI, CR
            start_summary = start_lam + lam_rows + 1
            df_summary = pd.DataFrame([
                {'Chỉ số': 'λ_max', 'Giá trị': round(lambda_max, 4)},
                {'Chỉ số': 'CI',    'Giá trị': round(CI, 4)},
                {'Chỉ số': 'CR',    'Giá trị': round(CR, 4)}
            ])
            df_summary.to_excel(writer, sheet_name=sheet_name, index=False, startrow=start_summary)

        # Khi rời khỏi with-block, ExcelWriter sẽ tự lưu
    buf.seek(0)
    return buf


@app.route('/export_excel')
def export_excel():
    buf = create_excel_bytes()
    return send_file(
        buf,
        download_name='report_ahp.xlsx',
        as_attachment=True,
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )



# --------- 2) EXPORT PDF ------------
@app.route('/export_pdf')
def export_pdf():
    now = datetime.now()
    html = render_template('result_pdf.html',
        results         = session['results'],
        crit_labels_vn  = session['crit_labels_vn'],
        alt_labels      = session['alt_labels'],
        alt_scores      = session['alt_scores'],
        crit_values     = session['crit_values'],
        lambda_max      = session['criteria_consistency']['lambda_max'],
        ci              = session['criteria_consistency']['CI'],
        cr              = session['criteria_consistency']['CR'],
        now             = now
    )
    pdf_bytes = HTML(string=html).write_pdf()
    return send_file(
      BytesIO(pdf_bytes),
      download_name='report_ahp.pdf',
      as_attachment=True,
      mimetype='application/pdf'
    )

# -------------------------------
# ROUTE: Cho phép khách hàng sửa lại giá trị của ma trận so sánh cặp và tính lại kết quả
# -------------------------------
from flask import jsonify, request, session, flash, redirect, url_for

@app.route('/recalc_option_matrix', methods=['POST'])
def recalc_option_matrix():
    crit = request.form.get('crit')
    alternatives = session.get('alternative_names', [])
    if not crit or not alternatives:
        flash("Thiếu thông tin tiêu chí hoặc phương án.")
        return redirect(url_for('matrix_display'))

    # Lấy ma trận gốc và sao chép
    matrices_detail = session.get('matrices_detail', {})
    if crit not in matrices_detail:
        flash("Không tìm thấy ma trận gốc để cập nhật.")
        return redirect(url_for('matrix_display'))
    matrix = [row[:] for row in matrices_detail[crit]['original']]

    # Cập nhật tam giác trên và dưới
    n = len(alternatives)
    for i in range(n):
        for j in range(i + 1, n):
            val_raw = request.form.get(f"matrix_{crit}_{i}_{j}")
            if val_raw is None or not validate_value(val_raw):
                flash(f"Giá trị ô ({i+1},{j+1}) không hợp lệ.")
                return redirect(url_for('matrix_display'))
            v = float(val_raw)
            matrix[i][j], matrix[j][i] = v, 1.0 / v

    # Tính AHP và kiểm tra nhất quán
    w, lam, ci, cr = compute_ahp(matrix)
    if cr >= 0.1:
        flash(f"Ma trận '{crit}' không nhất quán (CR = {cr:.3f}).")
        return redirect(url_for('matrix_display'))

    # Cập nhật lại session
    matrices_detail[crit] = {
        'original':   matrix,
        'normalized': (np.array(matrix)/np.array(matrix).sum(axis=0)).tolist(),
        'weights':    w.tolist(),
        'lambda_max': lam,
        'ci':         ci,
        'cr':         cr
    }
    session['matrices_detail'] = matrices_detail

    # Redirect về lại page chi tiết để render ma trận mới
    return redirect(url_for('matrix_display'))


# -------------------------------
# Hàm tính lại kết quả tổng thể dựa trên dữ liệu cập nhật của các ma trận cục bộ
# -------------------------------
def recalc_matrix():
    selected = session.get('selected_criteria', None)
    if not selected or not (2 <= len(selected) <= 7):
        flash("Chưa chọn số lượng tiêu chí hợp lệ.")
        return redirect(url_for('select_criteria_page'))
    
    crit_weights = session.get('crit_weights', None)
    if not crit_weights:
        flash("Chưa có trọng số tiêu chí, vui lòng nhập lại ma trận tiêu chí.")
        return redirect(url_for('save_criteria'))
    
    chosen_vehicles = session.get('selected_vehicles', None)
    alternative_names = session.get('alternative_names', None)
    if not chosen_vehicles or not alternative_names:
        flash("Chưa có thông tin public.xe cần đánh giá.")
        return redirect(url_for('select_vehicles'))
    
    sub_vectors = session.get('matrices_detail', {})
    
    results = []
    m_alt = len(alternative_names)
    for i in range(m_alt):
        score = 0.0
        for idx, crit in enumerate(selected):
            if crit in sub_vectors:
                score += crit_weights[idx] * sub_vectors[crit]['weights'][i]
        results.append({'name': alternative_names[i], 'score': score})
    
    total = sum(item['score'] for item in results)
    if total:
        for item in results:
            item['score'] /= total
    
    results = sorted(results, key=lambda x: x['score'], reverse=True)
    
    session['results'] = results
    return redirect(url_for('result'))

# -------------------------------
# ROUTE: Xem chi tiết ma trận so sánh cặp
# -------------------------------
@app.route('/ma-tran-phuong-an')
def matrix_display():
    matrices     = session.get('matrices_detail') or {}
    alternatives = session.get('alternative_names') or []
    if not matrices or not alternatives:
        flash("Chưa có dữ liệu ma trận so sánh cặp. Vui lòng thực hiện tính toán AHP trước.")
        return redirect(url_for('result'))
    # Lấy tên hiển thị tiếng Việt từ config
    full_cfg = get_criteria_config()
        # Gán table_name vào info để template dễ xài
    for crit, info in matrices.items():
               orig = info.get('original', [])
        # --- Chuẩn hoá mỗi ma trận info['original'] về size = n×n ---
    n = len(alternatives)
    for crit, info in matrices.items():
        orig = info.get('original', [])
        # Nếu orig không đầy đủ (hay nhỡ-user custom rồi missing 1 vài ô)
        if len(orig) != n or any(len(row) != n for row in orig):
            # Tạo ma trận 1.0 trên cả hai tam giác
            full = [[1.0]*n for _ in range(n)]
            # copy những ô có dữ liệu (nếu có)
            for i, row in enumerate(orig):
                for j, v in enumerate(row):
                    if 0 <= i < n and 0 <= j < n:
                        try:
                            full[i][j] = float(v)
                        except:
                            pass
            info['original'] = full
            # weights cũng có thể rebuild tạm (đều nhau)
            info['weights'] = [1.0/n]*n
    # Lưu lại session nếu muốn
    session['matrices_detail'] = matrices
    return render_template('matrix_display.html',
                           matrices=matrices,
                           alternatives=alternatives,
                           full_cfg=full_cfg)


# -------------------------------
# ROUTE: Tính lại kết quả tổng thể dựa trên các ma trận con đã cập nhật
# -------------------------------
@app.route('/recalc_total', methods=['POST'])
def recalc_total():
    selected = session.get('selected_criteria')
    crit_weights = session.get('crit_weights')
    alternatives = session.get('alternative_names')
    full_cfg = get_criteria_config()

    # 1) Build lại sub_vectors từ form, giống custom_matrix
    sub_vectors = {}
    for crit in selected:
        info = full_cfg[crit]
        n = len(alternatives)
        M = [[1.0]*n for _ in range(n)]
        for i in range(n):
            for j in range(i+1, n):
                key = f"matrix_{crit}_{i}_{j}"
                raw = request.form.get(key)
                if raw is None or not validate_value(raw):
                    flash(f"Giá trị ô ({i+1},{j+1}) của '{info['display']}' không hợp lệ.")
                    return render_template('matrix_display.html',
                                           matrices=session.get('matrices_detail'),
                                           alternatives=alternatives)
                v = float(raw)
                M[i][j], M[j][i] = v, 1.0/v
        w, _, _, cr = compute_ahp(M)
        if cr >= 0.1:
            flash(f"Ma trận '{info['display']}' không nhất quán (CR={cr:.3f}).")
            return render_template('matrix_display.html',
                                   matrices=session.get('matrices_detail'),
                                   alternatives=alternatives)
        sub_vectors[crit] = {'original': M, 'weights': w.tolist()}

    session['matrices_detail'] = sub_vectors

    # 2) Tính tổng AHP
    results = []
    for idx, name in enumerate(alternatives):
        score = sum(crit_weights[k] * sub_vectors[crit]['weights'][idx]
                    for k,crit in enumerate(selected))
        results.append({'name': name, 'score': score})
    total = sum(r['score'] for r in results)
    if total>0:
        for r in results: r['score'] /= total
    results.sort(key=lambda x: x['score'], reverse=True)

    session['results'] = results
    save_calculation_history(session.get('selected_vehicles'), selected, crit_weights, results, sub_vectors)

    # 3) Render luôn result.html với dữ liệu mới
    consistency = session.get('criteria_consistency', {})
    # chuẩn bị alt_labels, alt_scores, crit_labels, crit_values như trong route result()
    alt_labels = [r['name'] for r in results]
    alt_scores = [r['score'] for r in results]
    crit_labels = selected
    crit_values = crit_weights
    crit_labels_vn = [full_cfg[c]['display'] for c in selected]

    return render_template('result.html',
        results=results, crit_weights=crit_weights,
        lambda_max=consistency.get('lambda_max'),
        ci=consistency.get('CI'),
        cr=consistency.get('CR'),
        alt_labels=alt_labels, alt_scores=alt_scores,
        crit_labels=crit_labels, crit_values=crit_values,
        crit_labels_vn=crit_labels_vn
    )

# -------------------------------
# ROUTE: Xem lịch sử tính toán
# -------------------------------
@app.route('/history')
def history():
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT id, calc_time, vehicles, criteria, crit_weights, results FROM calculation_history ORDER BY calc_time DESC;")
        rows = cur.fetchall()
        conn.close()
        history_list = []
        for row in rows:
            history_list.append({
                'id': row[0],
                'calc_time': row[1],
                'vehicles': json.loads(row[2]),
                'criteria': json.loads(row[3]),
                'crit_weights': json.loads(row[4]) if row[4] else None,
                'results': json.loads(row[5]) if row[5] else None
            })
    except Exception as e:
        flash("Lỗi truy xuất lịch sử tính toán: " + str(e))
        history_list = []
    return render_template('history.html', history_list=history_list)


if __name__ == '__main__':
    app.run(debug=True)
