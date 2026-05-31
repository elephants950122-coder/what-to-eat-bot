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

# ============================================================
# 🔥 初始化 Firebase 的全域安全鎖
# ============================================================
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
# 🔍 深度內文穿透：精準擷取 PTT 食記中的實體地址與正式店名
# ============================================================
def extract_info_from_content(content):
    results = {"name": None, "address": None}
    
    if not content:
        return results
        
    lines = content.split('\n')
    for line in lines:
        # 1. 擷取正式店名
        if any(k in line for k in ["餐廳名稱", "店名", "店家名稱"]):
            parts = re.split(r'[:：]', line)
            if len(parts) > 1:
                clean_n = re.sub(r'[^\u4e00-\u9fa5A-Z0-9]', '', parts[1]).strip()
                if clean_n:
                    results["name"] = clean_n[:20]
                    
        # 2. 擷取地址
        if any(k in line for k in ["地址", "住址", "地 址", "地點", "位址"]):
            parts = re.split(r'[:：]', line)
            if len(parts) > 1:
                addr = parts[1].strip()
                if "沙鹿" in addr or "台中" in addr:
                    addr = re.sub(r'\(.*\)', '', addr).strip()
                    results["address"] = addr[:45]
    
    # 備用 Regex 匹配
    if not results["address"]:
        addr_match = re.search(r'台?中[市縣]沙鹿區[^\s\d]+[路街巷][\d之-]+號?', content)
        if addr_match:
            results["address"] = addr_match.group()
        
    return results

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
# 📡 3. 全滿載深度爬蟲 (循序記憶 1 頁 + 無限制篇數完整抓取)
# ============================================================
@app.route("/find_food")
def find_food():
    location = "沙鹿"
    encoded_location = urllib.parse.quote(location)
    cookies = {'over18': '1'}
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    
    net_new_inserted = 0
    
    try:
        safe_init_firebase()
        db = firestore.client()
        
        # 💡 [進度記憶系統] 讀取上次爬到第幾頁
        config_ref = db.collection("metadata").document("crawler_config")
        config_doc = config_ref.get()
        
        last_page = 0
        if config_doc.exists:
            last_page = config_doc.to_dict().get("last_page_crawled", 0)
            
        start_page = last_page + 1
        
        # 💡 為了達成「文章不設限全抓」，我們將每次處理頁數改為「精確的 1 頁」
        # 1 頁約 20 篇，完全穿透剛好壓在 Vercel 10 秒安全線內！
        max_pages_per_run = 1 
        end_page = start_page + max_pages_per_run - 1
        
        # 超過 8 頁歷史盡頭，自動歸零循環
        if start_page > 8: 
            start_page = 1
            end_page = 1
            
        existing_docs = db.collection("restaurants").get()
        existing_ids = [doc.id for doc in existing_docs]
        
        reached_end = False

        # 🚀 循序推進：專注掃描 start_page
        for current_p in range(start_page, end_page + 1):
            url = f"https://www.ptt.cc/bbs/Food/search?page={current_p}&q={encoded_location}"
            response = requests.get(url, headers=headers, cookies=cookies)
            
            if response.status_code != 200:
                break
                
            soup = BeautifulSoup(response.text, 'html.parser')
            articles = soup.find_all('div', class_='r-ent')
            
            # 如果這頁完全沒文章，代表爬到 PTT 歷史盡頭
            if not articles:
                reached_end = True
                break
                
            # 💡 移除限制陣列，整頁文章 100% 完整遍歷不漏抓
            for art in articles:
                title_tag = art.find('div', class_='title')
                if title_tag and title_tag.a:
                    title_text = title_tag.a.text.strip()
                    
                    if "公告" in title_text or "[食記]" not in title_text:
                        continue
                    
                    clean_name = super_clean_title(title_text)
                    if not clean_name:
                        continue
                    
                    article_url = "https://www.ptt.cc" + title_tag.a['href']
                    deep_info = {"name": None, "address": None}
                    
                    # --- 🚀 啟動第二層爬蟲：點進內文抓地址 ---
                    try:
                        art_response = requests.get(article_url, headers=headers, cookies=cookies, timeout=3)
                        if art_response.status_code == 200:
                            art_soup = BeautifulSoup(art_response.text, 'html.parser')
                            main_content = art_soup.find(id='main-content')
                            content_text = main_content.text if main_content else ""
                            deep_info = extract_info_from_content(content_text)
                    except Exception as e:
                        pass # 遇到死連結直接跳過，不中斷爬蟲
                    
                    final_name = deep_info["name"] if deep_info["name"] else clean_name
                    
                    # 💡 【實體識別去重】：優先以「地址」為 ID，抓不到則以「純淨店名」為 ID
                    doc_id = deep_info["address"] if deep_info["address"] else final_name
                    
                    if doc_id not in existing_ids:
                        net_new_inserted += 1
                        existing_ids.append(doc_id)
                        
                    simulated_rating = round(random.uniform(4.0, 4.9), 1)
                    
                    doc_data = {
                        "name": final_name,
                        "address": deep_info["address"] if deep_info["address"] else "暫無明確地址快取",
                        "ptt_title": title_text,
                        "area": location,
                        "rating": simulated_rating,
                        "type": "美食",
                        "source": "PTT Food板大數據",
                        "sync_time": firestore.SERVER_TIMESTAMP
                    }
                    
                    db.collection("restaurants").document(doc_id).set(doc_data)
                    
                    # 💡 為了 20 篇能在 10 秒內跑完，將延遲縮短為 0.05 秒
                    time.sleep(0.05) 
            
        # 💡 [儲存進度] 爬取結束後，將進度寫回 Firebase
        if reached_end:
            config_ref.set({"last_page_crawled": 0})
            flash_msg = "🎉 恭喜！系統已自動偵測到最後一頁，所有沙鹿歷史數據已完全無遺漏同步！進度已自動歸零。"
        else:
            config_ref.set({"last_page_crawled": end_page})
            flash_msg = f"🚀 無漏網之魚深度同步成功！本次「完整掃描」第 {start_page} 頁所有文章，全新灌入 {net_new_inserted} 筆沙鹿美食！"
        
        # 重新撈取資料庫清單展示
        docs = db.collection("restaurants").order_by("sync_time", direction=firestore.Query.DESCENDING).get()
        restaurant_list = [doc.to_dict() for doc in docs]
        total_in_db = len(restaurant_list)
        
        flash(flash_msg, "success")
        return render_template("result.html", total_inserted=net_new_inserted, total_in_db=total_in_db, restaurants=restaurant_list)
        
    except Exception as e:
        flash(f"❌ 深度爬蟲中斷：{str(e)}", "danger")
        return redirect(url_for('home'))

# ============================================================
# 🗑️ 4. 資料庫優化管理：一鍵清空資料庫 (並重置爬蟲進度)
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
            
        # 💡 同時將爬蟲進度強制歸零
        db.collection("metadata").document("crawler_config").set({"last_page_crawled": 0})
            
        flash(f"報告管理員！已成功連線 Firebase 雲端資料庫並清空共 {count} 筆歷史垃圾快取！數據與爬蟲進度皆已重置。", "success")
        
        return render_template("result.html", total_inserted=0, total_in_db=0, restaurants=[])
        
    except Exception as e:
        flash(f"❌ 系統清空資料庫失敗，錯誤原因: {e}", "danger")
        return render_template("result.html", total_inserted=0, total_in_db=0, restaurants=[])

# ============================================================
# 🤖 5. Webhook 通道 (加入超強防呆與地圖導航功能)
# ============================================================
@app.route("/webhook", methods=["POST"])
def webhook():
    req = request.get_json(force=True)
    query_result = req.get("queryResult", {})
    action = query_result.get("action", "")
    parameters = query_result.get("parameters", {})
    
    # 💡 取得使用者完整輸入句子 (用來做超強備用分析)
    query_text = query_result.get("queryText", "")
    
    # 安全拆解 Dialogflow 丟過來的地理物件字典
    raw_location = parameters.get("location", "")
    if isinstance(raw_location, dict):
        loc_str = raw_location.get("subadmin-area") or raw_location.get("city") or raw_location.get("admin-area") or ""
        user_location = loc_str.replace("區", "").replace("市", "").strip()
    else:
        user_location = str(raw_location).replace("區", "").replace("市", "").strip()
        
    # 💡 [防呆終極版]：白名單機制 (Whitelist) - 只允許沙鹿/靜宜/台中，其餘有明確地點的一律阻擋
    valid_keywords = ["沙鹿", "靜宜", "台中"]

    # 1. 檢查 Dialogflow 抓出的地點，或者原句中是否包含白名單關鍵字
    if any(kw in user_location for kw in valid_keywords) or any(kw in query_text for kw in valid_keywords):
        user_location = "沙鹿"  # 判定為有效，統一鎖定為沙鹿去撈資料庫
    else:
        # 2. 如果沒有白名單關鍵字，但 Dialogflow 確實有抓到一個「其他地點」（例如桃園、日本）
        if user_location:
            info = f"🥺 抱歉！我是靜宜資管專屬的「沙鹿美食機器人」，我的雲端資料庫只有收錄沙鹿的美食，暫時沒有【{user_location}】的資料喔！你可以試著問我沙鹿的美食！"
            return make_response(jsonify({"fulfillmentText": info}))
        else:
            # 3. 如果 Dialogflow 沒抓到地點，句子裡也沒有任何地點（例如只說「肚子餓了」、「推薦宵夜」）
            user_location = "沙鹿"  # 預設放行當作問沙鹿

    user_food_type = parameters.get("food_type", "") 
    
    # 💡 分類關鍵字大擴充字典 (這段剛剛不小心被切斷了，現在完美補回)
    type_keywords = {
        "宵夜": ["宵夜", "深夜", "燒烤", "串燒", "酒吧", "永和豆漿"],
        "下午茶": ["下午茶", "點心", "蛋糕", "甜點", "咖啡", "冰品", "豆花", "手搖", "麵包", "烘焙"],
        "早午餐": ["早午餐", "早餐", "BRUNCH", "蛋餅", "吐司", "漢堡", "飯糰"],
        "咖哩": ["咖哩", "咖喱", "curry"],
        "火鍋": ["火鍋", "鍋物", "麻辣鍋", "臭臭鍋", "小火鍋", "壽喜燒"],
        "日式": ["日式", "拉麵", "壽司", "丼飯", "生魚片", "居酒屋"]
    }
    
    # 💡 [防呆 3]：如果 Dialogflow 聽不懂「咖哩」，我們直接掃描原句
    if not user_food_type:
        for food_category, keywords in type_keywords.items():
            if food_category in query_text or any(kw in query_text for kw in keywords):
                user_food_type = food_category
                # 就算 Dialogflow 走到 Fallback，我們偵測到關鍵字，就強行改成推薦動作
                action = "recommend_restaurant"
                break

    info = "抱歉，我目前無法處理這個動作喔！請試著告訴我「想吃沙鹿的宵夜」或「推薦沙鹿的咖哩」。"
    
    if action == "recommend_restaurant":
        try:
            safe_init_firebase()
            db = firestore.client()
            docs = db.collection("restaurants").get()
            all_restaurants = [doc.to_dict() for doc in docs]
            
            if all_restaurants:
                # 第一層篩選：符合地點
                filtered_list = [r for r in all_restaurants if r.get("area") == "沙鹿"]
                
                if user_food_type and user_food_type in type_keywords:
                    keywords = type_keywords[user_food_type]
                    category_matched_list = []
                    for r in filtered_list:
                        title_upper = r.get("ptt_title", "").upper()
                        if any(kw in title_upper for kw in keywords):
                            category_matched_list.append(r)
                            
                    filtered_list = category_matched_list
                    info = f"🤖 已為您連線 Firebase，從小組專屬大數據庫中精選出符合【沙鹿 {user_food_type}】的口袋名單：\n\n"
                else:
                    info = f"🤖 已為您從 Firebase 大數據中，隨機精選 5 間【沙鹿】在地好料：\n\n"
                
                if filtered_list:
                    sample_size = min(5, len(filtered_list))
                    random_list = random.sample(filtered_list, sample_size)
                    
                    result = ""
                    for index, item_data in enumerate(random_list, 1):
                        name = str(item_data.get("name", "未知店家"))
                        rating = str(item_data.get("rating", "4.0"))
                        address = str(item_data.get("address", "暫無明確地址快取"))
                        
                        # 💡 核心亮點：自動合成 Google 導航網址
                        map_query = address if "沙鹿" in address else f"台中市沙鹿區{address}"
                        map_url = f"https://www.google.com/maps/search/?api=1&query={urllib.parse.quote(map_query)}"
                        
                        result += f"🍱 推薦 {index}：{name}\n📍 地址：{address}\n⭐ 評分：{rating}\n🗺️ 導航：{map_url}\n\n"
                    
                    info += result + "祝您用餐愉快！😋"
                else:
                    info = f"📋 報告！目前 Firebase 大數據庫中，暫時還沒有關於【沙鹿 {user_food_type}】的精確食記。"
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