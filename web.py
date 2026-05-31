import os
import json
import random
import time
import urllib.parse
import re
import requests
from flask import Flask, request, jsonify, make_response, render_template, flash, redirect, url_for
from bs4 import BeautifulSoup
import firebase_admin
from firebase_admin import credentials, firestore

# 動態取得當前 web.py 所在的絕對路徑
current_dir = os.path.dirname(os.path.abspath(__file__))

# 明確指定 templates 的絕對路徑，徹底防止 Vercel 找不到 index.html 與 result.html
app = Flask(__name__, template_folder=os.path.join(current_dir, "templates"))

# 啟用 Flask 內建快閃訊息功能 (Flash Messages) 必須設定的安全金鑰
app.secret_key = "providence_shalu_ultimate_key"

# 初始化 Firebase 的全域安全鎖
def safe_init_firebase():
    if not firebase_admin._apps:
        try:
            # 優先讀取你在 Vercel 設定好的 FIREBASE_KEY 環境變數
            if "FIREBASE_KEY" in os.environ:
                key_json_str = os.environ["FIREBASE_KEY"].strip()
                key_dict = json.loads(key_json_str)
                cred = credentials.Certificate(key_dict)
                firebase_admin.initialize_app(cred)
            else:
                # 本地測試時自動讀取實體金鑰檔案
                local_key = os.path.join(current_dir, "serviceAccountKey.json")
                if os.path.exists(local_key):
                    cred = credentials.Certificate(local_key)
                    firebase_admin.initialize_app(cred)
                else:
                    raise FileNotFoundError("找不到任何 Firebase 金鑰設定（環境變數或本地 JSON 檔）")
            print("✅ [Firebase 連線] 成功建立通道！")
        except Exception as e:
            print(f"❌ [Firebase 連線失敗]：{e}")
            raise e

# ============================================================
# 🧼 終極資料清洗器：精準濾除 [食記]、引號、符號，只留純中英數
# ============================================================
def super_clean_title(raw_title):
    if not raw_title:
        return ""
        
    name = raw_title.upper()
    
    # 移除常見的 PTT 標籤與地區贅字
    garbage_list = [
        "[食記]", "食記", "台中市", "台中", "沙鹿區", "沙鹿", "FW:", "FW", "推薦", 
        "必吃", "好吃", "超強", "終於吃到", "隱藏版", "排隊", "平價", "美味", "老店", "大推"
    ]
    for garbage in garbage_list:
        name = name.replace(garbage, "")
    
    # 💡 核心大絕招：只保留 中文字、英文字母、數字（所有標點、全半型點點、引號、空格全數蒸發）
    name = re.sub(r'[^\u4e00-\u9fa5A-Z0-9]', '', name)
    
    # 強制剔除因順序殘留在頭部的地區字眼
    front_garbage = ["區", "市", "鎮", "鄉"]
    while len(name) > 0 and name[0] in front_garbage:
        name = name[1:]
        
    return name.strip()[:20]

# ============================================================
# 🔍 深度內文穿透：精準擷取 PTT 食記中的實體地址
# ============================================================
def extract_address_from_content(content):
    if not content:
        return None
        
    # 策略 1：依行尋找地址關鍵字標籤
    lines = content.split('\n')
    for line in lines:
        if any(k in line for k in ["地址", "住址", "地 址", "地點", "位址"]):
            parts = re.split(r'[:：]', line)
            if len(parts) > 1:
                addr = parts[1].strip()
                if "沙鹿" in addr or "台中" in addr:
                    addr = re.sub(r'\(.*\)', '', addr).strip() # 移除括號備註
                    return addr[:45]
                    
    # 策略 2：用正規表達式匹配標準台中沙鹿地址格式
    addr_match = re.search(r'台?中[市縣]沙鹿區[^\s\d]+[路街巷][\d之-]+號?', content)
    if addr_match:
        return addr_match.group()
        
    return None

# ============================================================
# 🏠 1. 管理後台首頁
# ============================================================
@app.route("/")
def home():
    return render_template("index.html")

# ============================================================
# 🤖 2. 網頁免登入對話測試端 (webdamo.html)
# ============================================================
@app.route("/chat")
def chat_page():
    return render_template("webdamo.html")

# ============================================================
# 📡 3. 雙層穿透式爬蟲 (8頁隨機跳躍 + 地址指紋去重)
# ============================================================
@app.route("/find_food")
def find_food():
    location = "沙鹿"
    encoded_location = urllib.parse.quote(location)
    
    # 💡 核心優化：隨機從 1 到 8 頁出發，多點幾次就能挖到不同的老店，突破第一頁魔咒！
    random_start_page = random.randint(1, 8)
    url = f"https://www.ptt.cc/bbs/Food/search?page={random_start_page}&q={encoded_location}"
    
    cookies = {'over18': '1'}
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    
    net_new_inserted = 0  # 僅記錄「真正新加入」的店家
    processed_count = 0   # 記錄處理的文章數，防止 Vercel 10秒超時
    
    try:
        safe_init_firebase()
        db = firestore.client()
        
        # 預先抓取資料庫中現有的所有 ID 列表，用於快速去重比對
        existing_docs = db.collection("restaurants").get()
        existing_ids = [doc.id for doc in existing_docs]
        
        while url and processed_count < 15:  # 限制單輪最大處理文章數，防範伺服器 504 崩潰
            response = requests.get(url, headers=headers, cookies=cookies)
            if response.status_code != 200:
                break
                
            soup = BeautifulSoup(response.text, 'html.parser')
            articles = soup.find_all('div', class_='r-ent')
            if not articles:
                break
                
            for art in articles:
                # 如果已經處理夠多文章，提早結束以保證網頁能安全回傳
                if processed_count >= 15:
                    break
                    
                title_tag = art.find('div', class_='title')
                if title_tag and title_tag.a:
                    title_text = title_tag.a.text.strip()
                    
                    if "公告" in title_text or "[食記]" not in title_text:
                        continue
                    
                    clean_name = super_clean_title(title_text)
                    if not clean_name:
                        continue
                    
                    # 抓取內文的詳細 URL 連結
                    article_url = "https://www.ptt.cc" + title_tag.a['href']
                    found_address = None
                    
                    # --- 🚀 啟動第二層爬蟲：點進內文抓地址 ---
                    try:
                        art_response = requests.get(article_url, headers=headers, cookies=cookies, timeout=3)
                        if art_response.status_code == 200:
                            art_soup = BeautifulSoup(art_response.text, 'html.parser')
                            main_content = art_soup.find(id='main-content')
                            content_text = main_content.text if main_content else ""
                            found_address = extract_address_from_content(content_text)
                    except Exception as e:
                        print(f"⚠️ [內文解析跳過] {article_url} 連線超時: {e}")
                    
                    # 💡 【實體識別去重】：優先以「地址」為 ID，抓不到則以「純淨店名」為 ID
                    doc_id = found_address if found_address else clean_name
                    
                    # 判斷是否為全新加入資料庫的資料
                    if doc_id not in existing_ids:
                        net_new_inserted += 1
                        existing_ids.append(doc_id)  # 動態寫入快取，防同一輪重覆計算
                        
                    simulated_rating = round(random.uniform(4.0, 4.9), 1)
                    
                    doc_data = {
                        "name": clean_name,
                        "address": found_address if found_address else "暫無明確地址快取",
                        "ptt_title": title_text,
                        "area": location,
                        "rating": simulated_rating,
                        "type": "美食",
                        "source": "PTT Food板大數據",
                        "sync_time": firestore.SERVER_TIMESTAMP
                    }
                    
                    # 執行寫入 (同 ID 自動覆蓋)
                    db.collection("restaurants").document(doc_id).set(doc_data)
                    processed_count += 1
                    time.sleep(0.1)
            
            # 自動尋找 PTT 的「‹ 上頁」按鈕
            btn_tags = soup.find_all('a', class_='btn wide')
            url = None 
            for btn in btn_tags:
                if "上頁" in btn.text and 'href' in btn.attrs:
                    url = "https://www.ptt.cc" + btn['href']
                    break
            
            time.sleep(0.3)
            
        # 重新撈取 Firebase 中不重複的所有美食清單，展示在結果頁
        docs = db.collection("restaurants").order_by("sync_time", direction=firestore.Query.DESCENDING).get()
        restaurant_list = [doc.to_dict() for doc in docs]
        total_in_db = len(restaurant_list)
        
        flash(f"🚀 大數據同步成功！本次隨機由第 {random_start_page} 頁發動穿透，全新灌入 {net_new_inserted} 筆沙鹿美食！", "success")
        return render_template("result.html", total_inserted=net_new_inserted, total_in_db=total_in_db, restaurants=restaurant_list)
        
    except Exception as e:
        flash(f"❌ 深度爬蟲中斷：{str(e)}", "danger")
        return redirect(url_for('home'))

# ============================================================
# 🗑️ 4. 資料庫優化管理：一鍵清空資料庫
# ============================================================
@app.route("/delete_all")
def delete_all():
    try:
        safe_init_firebase()
        db = firestore.client()
        docs = db.collection("restaurants").get()
        count = 0
        for doc in docs:
            db.collection("restaurants").document(doc.id).delete()
            count += 1
            
        flash(f"報告管理員！已成功連線 Firebase 雲端資料庫並清空共 {count} 筆歷史垃圾快取！數據已重置。", "success")
        return redirect(url_for('home'))
    except Exception as e:
        flash(f"❌ 系統清空資料庫失敗，錯誤原因: {e}", "danger")
        return redirect(url_for('home'))

# ============================================================
# 🤖 5. Webhook 通道 (LINE 機器人核心對接組件 - 支援分類篩選與地址)
# ============================================================
@app.route("/webhook", methods=["POST"])
def webhook():
    req = request.get_json(force=True)
    query_result = req.get("queryResult", {})
    action = query_result.get("action", "")
    parameters = query_result.get("parameters", {})
    
    # 安全拆解 Dialogflow 丟過來的地理物件字典
    raw_location = parameters.get("location", "沙鹿")
    if isinstance(raw_location, dict):
        loc_str = raw_location.get("subadmin-area") or raw_location.get("city") or raw_location.get("admin-area") or "沙鹿"
        user_location = loc_str.replace("區", "").replace("市", "").strip()
    else:
        user_location = str(raw_location).replace("區", "").replace("市", "").strip()
        
    if not user_location:
        user_location = "沙鹿"

    user_food_type = parameters.get("food_type", "") 
    info = "抱歉，我目前無法處理這個動作喔！"
    
    if action == "recommend_restaurant":
        try:
            safe_init_firebase()
            db = firestore.client()
            docs = db.collection("restaurants").get()
            all_restaurants = [doc.to_dict() for doc in docs]
            
            if all_restaurants:
                # 第一層篩選：符合地點
                filtered_list = [r for r in all_restaurants if r.get("area") == user_location]
                
                # 第二層篩選：分類對照
                type_keywords = {
                    "宵夜": ["宵夜", "宵夜", "深夜", "燒烤", "串燒", "酒吧", "永和豆漿"],
                    "下午茶": ["下午茶", "點心", "蛋糕", "甜點", "咖啡", "冰品", "豆花", "手搖", "麵包", "烘焙"],
                    "早午餐": ["早午餐", "早餐", "BRUNCH", "蛋餅", "吐司", "漢堡", "飯糰"]
                }
                
                if user_food_type and user_food_type in type_keywords:
                    keywords = type_keywords[user_food_type]
                    category_matched_list = []
                    for r in filtered_list:
                        title_upper = r.get("ptt_title", "").upper()
                        if any(kw in title_upper for kw in keywords):
                            category_matched_list.append(r)
                            
                    filtered_list = category_matched_list
                    info = f"🤖 已為您連線 Firebase，從小組專屬大數據庫中精選出符合【{user_location} {user_food_type}】的口袋名單：\n\n"
                else:
                    info = f"🤖 已為您從 Firebase 大數據中，隨機精選 5 間【{user_location}】在地好料：\n\n"
                
                if filtered_list:
                    sample_size = min(5, len(filtered_list))
                    random_list = random.sample(filtered_list, sample_size)
                    
                    result = ""
                    for index, item_data in enumerate(random_list, 1):
                        name = str(item_data.get("name", "未知店家"))
                        rating = str(item_data.get("rating", "4.0"))
                        address = str(item_data.get("address", "暫無明確地址快取"))
                        
                        result += f"🍱 推薦 {index}：{name}\n📍 店家地址：{address}\n⭐ 鄉民評分：{rating}\n\n"
                    
                    info += result + "祝您用餐愉快！😋"
                else:
                    info = f"📋 報告！目前 Firebase 大數據庫中，暫時還沒有關於【{user_location} {user_food_type}】的精確食記。"
            else:
                info = "📋 目前資料庫內暫無美食資料，請先前往管理後端進行網頁爬取同步！"
        except Exception as e:
            info = f"❌ 後端執行錯誤，原因: {str(e)}"

    # 顯示完整資料庫清單的動作 (GetFoodList)
    elif action == "GetFoodList":
        try:
            safe_init_firebase()
            db = firestore.client()
            docs = db.collection("restaurants").get()
            
            titles = []
            for doc in docs:
                item_data = doc.to_dict()
                if item_data.get("name"):
                    titles.append(str(item_data.get("name")))
                    
            if titles:
                unique_titles = list(set(titles))
                info = "📋 目前資料庫收錄的沙鹿美食有：\n\n-- " + "\n-- ".join(unique_titles[:30])
            else:
                info = "📋 目前資料庫內暫無美食資料。"
        except Exception as e:
            info = f"❌ 讀取清單失敗，原因: {str(e)}"

    return make_response(jsonify({"fulfillmentText": info}))

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=5000, debug=True)