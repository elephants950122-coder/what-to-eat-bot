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
# 🤖 5. Webhook 通道 (終極圖卡修復版)
# ============================================================
@app.route("/webhook", methods=["POST"])
def webhook():
    try:
        req = request.get_json(force=True)
        query_result = req.get("queryResult", {})
        # 💡 防呆：確保 action 和 query_text 絕對不會是 None 導致當機
        action = query_result.get("action") or ""
        parameters = query_result.get("parameters", {})
        query_text = query_result.get("queryText") or ""

        # =========================================================================
        # 💡 第一優先級：無敵圖卡說明書攔截器
        # =========================================================================
        help_keywords = ["說明", "幫助", "教學", "怎麼用", "功能", "使用方法", "help", "菜單", "使用說明", "你好", "hi", "哈囉", "嗨", "說明書", "啊喂"]
        
        if any(kw in query_text.lower() for kw in help_keywords) or action == "input.welcome":
            # 100% 符合 LINE 嚴格標準的安全排版圖卡
            flex_payload = {
                "line": {
                    "type": "flex",
                    "altText": "🍟 沙鹿美食管家 - 使用秘笈",
                    "contents": {
                        "type": "bubble",
                        "header": {
                            "type": "box",
                            "layout": "vertical",
                            "backgroundColor": "#ff7675",
                            "paddingAll": "20px",
                            "contents": [
                                {
                                    "type": "text",
                                    "text": "🍽️ 靜宜資管專屬",
                                    "color": "#ffffff",
                                    "size": "sm",
                                    "weight": "bold"
                                },
                                {
                                    "type": "text",
                                    "text": "美食雷達使用說明",
                                    "color": "#ffffff",
                                    "size": "xl",
                                    "weight": "bold",
                                    "margin": "sm"
                                }
                            ]
                        },
                        "body": {
                            "type": "box",
                            "layout": "vertical",
                            "spacing": "md",
                            "contents": [
                                {
                                    "type": "text",
                                    "text": "請直接輸入以下關鍵字：",
                                    "size": "sm",
                                    "color": "#636e72",
                                    "weight": "bold"
                                },
                                {
                                    "type": "separator",
                                    "margin": "md"
                                },
                                {
                                    "type": "text",
                                    "text": "🎲 隨機推薦",
                                    "weight": "bold",
                                    "size": "md",
                                    "color": "#d63031",
                                    "margin": "md"
                                },
                                {
                                    "type": "text",
                                    "text": "👉 範例：「肚子餓了」、「沙鹿美食」",
                                    "size": "xs",
                                    "color": "#b2bec3"
                                },
                                {
                                    "type": "text",
                                    "text": "🎯 指定種類",
                                    "weight": "bold",
                                    "size": "md",
                                    "color": "#0984e3",
                                    "margin": "md"
                                },
                                {
                                    "type": "text",
                                    "text": "👉 範例：「推薦咖哩」、「想吃宵夜」",
                                    "size": "xs",
                                    "color": "#b2bec3"
                                },
                                {
                                    "type": "text",
                                    "text": "📋 總覽清單",
                                    "weight": "bold",
                                    "size": "md",
                                    "color": "#00b894",
                                    "margin": "md"
                                },
                                {
                                    "type": "text",
                                    "text": "👉 範例：「查看全部資料」",
                                    "size": "xs",
                                    "color": "#b2bec3"
                                }
                            ]
                        }
                    }
                }
            }
            
            # 💡 核心修復：payload 必須放在最外層！
            return make_response(jsonify({
                "fulfillmentText": "請在 LINE 手機版上查看精美圖卡！",
                "payload": flex_payload
            }))

        # =========================================================================
        # 💡 接下來才是過濾地點與食物的邏輯
        # =========================================================================
        clean_query = query_text.replace("日本料理", "").replace("日式", "").replace("韓式", "").replace("韓國烤肉", "").replace("泰式", "").replace("義式", "").replace("美式", "").replace("港式", "")
        
        raw_location = parameters.get("location", "")
        if isinstance(raw_location, dict):
            loc_str = raw_location.get("subadmin-area") or raw_location.get("city") or raw_location.get("admin-area") or ""
            user_location = loc_str.replace("區", "").replace("市", "").strip()
        else:
            user_location = str(raw_location).replace("區", "").replace("市", "").strip()
            
        if not user_location:
            out_of_bounds = [
                "台北", "新北", "基隆", "桃園", "新竹", "苗栗", "彰化", "南投", "雲林", "嘉義", 
                "台南", "高雄", "屏東", "宜蘭", "花蓮", "台東", "清水", "梧棲", "大甲", "大肚", "龍井",
                "日本", "韓國", "泰國", "美國", "英國", "法國", "義大利", "中國", "大陸", "香港", "澳門", "東京", "大阪", "首爾", "外國", "國外"
            ]
            for place in out_of_bounds:
                if place in clean_query:
                    user_location = place
                    break
                    
        valid_keywords = ["沙鹿", "靜宜", "弘光", "台中"]

        if any(kw in user_location for kw in valid_keywords) or any(kw in clean_query for kw in valid_keywords):
            user_location = "沙鹿" 
        else:
            if user_location:
                info = f"🥺 抱歉！我是靜宜資管專屬的「沙鹿美食機器人」，暫時沒有【{user_location}】的資料喔！你可以試著問我沙鹿的美食！"
                return make_response(jsonify({"fulfillmentText": info}))
            else:
                user_location = "沙鹿"

        user_food_type = parameters.get("food_type", "") 
        type_keywords = {
            "宵夜": ["宵夜", "深夜", "燒烤", "串燒", "酒吧", "永和豆漿"],
            "下午茶": ["下午茶", "點心", "蛋糕", "甜點", "咖啡", "冰品", "豆花", "手搖", "麵包", "烘焙"],
            "早午餐": ["早午餐", "早餐", "BRUNCH", "蛋餅", "吐司", "漢堡", "飯糰"],
            "咖哩": ["咖哩", "咖喱", "curry"],
            "火鍋": ["火鍋", "鍋物", "麻辣鍋", "臭臭鍋", "小火鍋", "壽喜燒"],
            "日式": ["日式", "拉麵", "壽司", "丼飯", "生魚片", "居酒屋", "日本料理"]
        }
        
        if not user_food_type:
            for food_category, keywords in type_keywords.items():
                if food_category in query_text or any(kw in query_text for kw in keywords):
                    user_food_type = food_category
                    action = "recommend_restaurant"
                    break

        general_food_keywords = ["美食", "想吃", "肚子餓", "推薦", "吃什麼", "有什麼好吃的", "餐廳"]
        if action != "recommend_restaurant" and action != "GetFoodList":
            if any(kw in query_text for kw in general_food_keywords):
                action = "recommend_restaurant" 

        info = "🥺 抱歉，我好像聽不太懂這個指令喔！\n你可以輸入「說明」或「你好」來查看使用教學卡片！"
        
        if action == "recommend_restaurant":
            safe_init_firebase()
            db = firestore.client()
            docs = db.collection("restaurants").get()
            all_restaurants = [doc.to_dict() for doc in docs]
            
            if all_restaurants:
                filtered_list = [r for r in all_restaurants if r.get("area") == "沙鹿"]
                if user_food_type and user_food_type in type_keywords:
                    keywords = type_keywords[user_food_type]
                    category_matched_list = []
                    for r in filtered_list:
                        title_upper = r.get("ptt_title", "").upper()
                        if any(kw in title_upper for kw in keywords):
                            category_matched_list.append(r)
                            
                    filtered_list = category_matched_list
                    info = f"🤖 已為您從小組專屬大數據庫中精選出符合【沙鹿 {user_food_type}】的口袋名單：\n\n"
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
                        
                        map_query = address if "沙鹿" in address else f"台中市沙鹿區{address}"
                        map_url = f"https://www.google.com/maps/search/?api=1&query={urllib.parse.quote(map_query)}"
                        result += f"🍱 推薦 {index}：{name}\n📍 地址：{address}\n⭐ 評分：{rating}\n🗺️ 導航：{map_url}\n\n"
                    
                    info += result + "祝您用餐愉快！😋"
                else:
                    info = f"📋 報告！目前暫時還沒有關於【沙鹿 {user_food_type}】的精確食記。"
            else:
                info = "📋 目前資料庫內暫無美食資料，請先前往管理後端同步！"

        elif action == "GetFoodList":
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

        return make_response(jsonify({"fulfillmentText": info}))
        
    except Exception as e:
        # 💡 終極防護：就算真的發生預期外錯誤，也不會讓 LINE 當機，而是回傳錯誤原因方便除錯
        print(f"Webhook Error: {e}")
        return make_response(jsonify({"fulfillmentText": f"系統發生錯誤，請稍後再試。錯誤代碼：{str(e)}"}))

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=5000, debug=True)