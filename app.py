import streamlit as st
import json
import os
from datetime import date
from PIL import Image
from google import genai
from pydantic import BaseModel, Field

# --- НАСТРОЙКА СТРАНИЦЫ ПОД МОБИЛЬНЫЙ ЭКРАН ---
st.set_page_config(
    page_title="Семейный Нутрициолог",
    page_icon="🥗",
    layout="centered",
    initial_sidebar_state="collapsed"
)

# Стилизация кнопок и полей для удобного нажатия пальцем
st.markdown("""
    <style>
    .stButton>button {
        width: 100%;
        height: 3.2em;
        font-size: 18px !important;
        font-weight: 600;
        border-radius: 12px;
    }
    div[data-testid="stNumberInput"] input, div[data-testid="stTextInput"] input {
        font-size: 16px !important;
    }
    </style>
""", unsafe_allow_html=True)

# --- ИНИЦИАЛИЗАЦИЯ API ---
# Ключ берется из секретов Streamlit Cloud или переменных окружения
API_KEY = st.secrets.get("GEMINI_API_KEY", os.environ.get("GEMINI_API_KEY", ""))
client = genai.Client(api_key=API_KEY) if API_KEY else None

# --- СТРУКТУРА ОТВЕТА ИИ (СТРОГИЙ JSON) ---
class IngredientDetail(BaseModel):
    name: str = Field(description="Название ингредиента")
    raw_weight_g: float = Field(description="Исходный вес в граммах")
    kcal: float = Field(description="Калории на весь исходный вес")
    protein: float = Field(description="Белки (г) на весь исходный вес")
    fat: float = Field(description="Жиры (г) на весь исходный вес")
    carbs: float = Field(description="Углеводы (г) на весь исходный вес")
    source: str = Field(description="Источник: 'Из фото этикетки' или 'База продуктов'")

class DishCalculation(BaseModel):
    dish_name: str = Field(description="Понятное название получившегося блюда")
    raw_total_weight_g: float = Field(description="Сумма весов сырых ингредиентов (г)")
    cooked_total_weight_g: float = Field(description="Итоговый вес ГОТОВОГО блюда (г) с учетом впитывания воды (крупы, макароны) или ужарки/уварки (мясо, рыба, овощи)")
    weight_change_explanation: str = Field(description="Краткое и понятное объяснение, почему изменился вес (например: 'Рис впитал воду и увеличился в 2.8 раза, курица ужарилась на 25%')")
    total_kcal: float
    total_protein: float
    total_fat: float
    total_carbs: float
    per_100g_kcal: float = Field(description="Ккал на 100 г ГОТОВОГО блюда")
    per_100g_protein: float = Field(description="Белки на 100 г ГОТОВОГО блюда")
    per_100g_fat: float = Field(description="Жиры на 100 г ГОТОВОГО блюда")
    per_100g_carbs: float = Field(description="Углеводы на 100 г ГОТОВОГО блюда")
    ingredients_breakdown: list[IngredientDetail]
    simple_advice: str = Field(description="1-2 очень простых и добрых совета, как сделать это блюдо еще полезнее под цель пользователя")

# --- БАЗА ДАННЫХ ПРОФИЛЕЙ И ДНЕВНИКА ---
DATA_FILE = "family_nutrition_data.json"

def load_data():
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {
        "Мама": {"age": 52, "height": 164, "weight": 68.0, "gender": "Ж", "goal": "Плавно снизить вес", "history": {}},
        "Папа": {"age": 55, "height": 178, "weight": 86.0, "gender": "М", "goal": "Поддерживать форму и здоровье", "history": {}}
    }

def save_data(data):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

data = load_data()

# --- ШАПКА: ВЫБОР ЧЕЛОВЕКА ---
st.title("🥗 Мой рацион")
selected_person = st.radio(
    "Кто сейчас заполняет дневник?",
    options=list(data.keys()),
    horizontal=True
)

profile = data[selected_person]
today_str = str(date.today())
if today_str not in profile["history"]:
    profile["history"][today_str] = {"meals": [], "steps": 6000, "active_kcal": 250}

today_log = profile["history"][today_str]

# --- РАСЧЕТ НОРМЫ КБЖУ (Миффлин - Сан Жеор + Часы) ---
def calculate_targets(p, steps, active_kcal):
    # Базовый обмен (BMR)
    if p["gender"] == "М":
        bmr = 10 * p["weight"] + 6.25 * p["height"] - 5 * p["age"] + 5
    else:
        bmr = 10 * p["weight"] + 6.25 * p["height"] - 5 * p["age"] - 161
    
    # Бытовая активность + данные с часов (шаги и активные калории)
    base_tdee = bmr * 1.2
    watch_bonus = active_kcal if active_kcal > 0 else (steps * 0.035)
    tdee = base_tdee + watch_bonus

    if p["goal"] == "Плавно снизить вес":
        target_kcal = max(bmr, tdee - 350)
        protein_g = p["weight"] * 1.5
        fat_g = p["weight"] * 0.9
    elif p["goal"] == "Набрать мышечную массу":
        target_kcal = tdee + 250
        protein_g = p["weight"] * 1.7
        fat_g = p["weight"] * 1.0
    else:
        target_kcal = tdee
        protein_g = p["weight"] * 1.3
        fat_g = p["weight"] * 1.0

    carbs_g = max(50, (target_kcal - (protein_g * 4 + fat_g * 9)) / 4)
    return round(target_kcal), round(protein_g), round(fat_g), round(carbs_g)

# --- НАВИГАЦИЯ ПО ВКЛАДКАМ ---
tab_calc, tab_day, tab_profile = st.tabs([
    "🍲 Посчитать блюдо", 
    "📊 Дневник и Часы", 
    "👤 Мои параметры"
])

# ==========================================
# ВКЛАДКА 1: КАЛЬКУЛЯТОР БЛЮДА ПО ИНГРЕДИЕНТАМ
# ==========================================
with tab_calc:
    st.subheader(f"Расчет блюда ({selected_person})")
    st.caption("Вводите вес **сырых** продуктов. Приложение само учтет, как изменится вес после варки или жарки.")

    if "ing_count" not in st.session_state:
        st.session_state.ing_count = 2

    col_add, col_rem = st.columns(2)
    with col_add:
        if st.button("➕ Добавить ингредиент"):
            st.session_state.ing_count += 1
    with col_rem:
        if st.session_state.ing_count > 1 and st.button("➖ Убрать последний"):
            st.session_state.ing_count -= 1

    ingredients_input = []
    for i in range(1, st.session_state.ing_count + 1):
        with st.container(border=True):
            st.markdown(f"**Ингредиент №{i}**")
            name = st.text_input(f"Название продукта", key=f"name_{i}", placeholder="Например: Овсянка / Куриное филе / Рис")
            weight = st.number_input(f"Вес СЫРОГО продукта (в граммах)", min_value=0, step=10, key=f"weight_{i}")
            photo = st.file_uploader(
                f"📷 Фото КБЖУ с упаковки (если есть)", 
                type=["jpg", "jpeg", "png", "webp"], 
                key=f"photo_{i}"
            )
            ingredients_input.append({"index": i, "name": name, "weight": weight, "photo": photo})

    cooking_method = st.text_area(
        "🍳 Как готовили блюдо?",
        placeholder="Например: Сварила кашу на воде (добавила 300 мл воды) / Потушила на сковороде под крышкой без масла / Нарезала сырым в салат",
        height=90
    )

    if st.button("🧮 Рассчитать КБЖУ готового блюда", type="primary"):
        valid_ings = [ing for ing in ingredients_input if ing["name"].strip() and ing["weight"] > 0]
        if not API_KEY:
            st.error("Не настроен ключ API! Добавьте GEMINI_API_KEY в настройки.")
        elif not valid_ings:
            st.warning("Пожалуйста, укажите хотя бы один ингредиент и его вес больше 0 г.")
        else:
            with st.spinner("Считаем КБЖУ, проверяем этикетки и учитываем уварку/ужарку..."):
                prompt_parts = [
                    "Ты профессиональный технолог питания и нутрициолог. "
                    "Рассчитай КБЖУ итогового ГОТОВОГО блюда. "
                    "ВАЖНЫЕ ПРАВИЛА:\n"
                    "1. Все веса указаны для СЫРЫХ (сухих) продуктов.\n"
                    "2. Обязательно пересчитай итоговый вес готового блюда (`cooked_total_weight_g`) по технологическим таблицам:\n"
                    "   - Крупы (рис, гречка, овсянка) и макароны при варке впитывают воду и увеличивают вес в 2.2–3.5 раза (при этом их общая калорийность не растет, но калорийность на 100 г готового продукта падает!). Если в способе приготовления явно указан объем добавленной воды/бульона, учитывай его.\n"
                    "   - Мясо, птица, рыба при жарке/тушении/запекании теряют влагу и уменьшаются в весе на 20–35% (концентрация КБЖУ на 100 г растет).\n"
                    "   - Овощи при тушении/жарке теряют вес, в сыром салате — вес равен сумме ингредиентов.\n"
                    "3. Если к ингредиенту приложено фото упаковки, возьми КБЖУ на 100 г строго с этого фото. Если фото нет — используй точные справочные данные для сырого продукта.\n"
                    f"4. Профиль пользователя: {selected_person}, цель: '{profile['goal']}'.\n"
                    f"5. Способ приготовления: {cooking_method if cooking_method else 'Стандартное приготовление'}.\n\n"
                    "Список сырых ингредиентов:\n"
                ]

                for ing in valid_ings:
                    prompt_parts.append(f"- Ингредиент №{ing['index']}: {ing['name']}, сырой вес: {ing['weight']} г.")
                    if ing["photo"] is not None:
                        img = Image.open(ing["photo"])
                        prompt_parts.append(f"Фото этикетки для Ингредиента №{ing['index']}:")
                        prompt_parts.append(img)

                response = client.models.generate_content(
                    model="gemini-2.5-flash",
                    contents=prompt_parts,
                    config=genai.types.GenerateContentConfig(
                        response_mime_type="application/json",
                        response_schema=DishCalculation,
                        temperature=0.1
                    )
                )
                st.session_state.last_calc = json.loads(response.text)

    # Отображение результата расчета
    if "last_calc" in st.session_state:
        res = st.session_state.last_calc
        st.success(f"✅ Расчет готов: **{res['dish_name']}**")
        
        st.markdown("### ⚖️ На 100 грамм ГОТОВОГО блюда:")
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Ккал", f"{round(res['per_100g_kcal'])}")
        c2.metric("Белки", f"{round(res['per_100g_protein'], 1)} г")
        c3.metric("Жиры", f"{round(res['per_100g_fat'], 1)} г")
        c4.metric("Углеводы", f"{round(res['per_100g_carbs'], 1)} г")

        st.info(
            f"**Как изменился вес:** Сырые продукты весили **{round(res['raw_total_weight_g'])} г** ➡️ "
            f"Готовое блюдо весит примерно **{round(res['cooked_total_weight_g'])} г**.\n\n"
            f"*{res['weight_change_explanation']}*"
        )

        with st.expander("📋 Подробно по каждому ингредиенту и всему блюду"):
            st.write(f"**Во всем объеме блюда ({round(res['cooked_total_weight_g'])} г):** "
                     f"{round(res['total_kcal'])} ккал | Б: {round(res['total_protein'], 1)} г | "
                     f"Ж: {round(res['total_fat'], 1)} г | У: {round(res['total_carbs'], 1)} г")
            for item in res["ingredients_breakdown"]:
                st.write(f"- **{item['name']}** ({round(item['raw_weight_g'])} г сырого): "
                         f"{round(item['kcal'])} ккал (Б:{round(item['protein'],1)}, Ж:{round(item['fat'],1)}, У:{round(item['carbs'],1)}) — *{item['source']}*")

        st.markdown(f"💡 **Совет:** {res['simple_advice']}")

        st.divider()
        st.markdown("#### 🍽 Записать съеденную порцию в сегодняшний дневник")
        eaten_grams = st.number_input(
            "Сколько грамм ГОТОВОГО блюда вы съели?", 
            min_value=10, 
            max_value=2000, 
            value=min(250, int(res["cooked_total_weight_g"])), 
            step=10
        )
        if st.button("📥 Добавить порцию в мой день"):
            ratio = eaten_grams / 100.0
            meal_entry = {
                "name": res["dish_name"],
                "grams": eaten_grams,
                "kcal": round(res["per_100g_kcal"] * ratio),
                "p": round(res["per_100g_protein"] * ratio, 1),
                "f": round(res["per_100g_fat"] * ratio, 1),
                "c": round(res["per_100g_carbs"] * ratio, 1)
            }
            today_log["meals"].append(meal_entry)
            save_data(data)
            st.toast(f"Добавлено в дневник ({selected_person})!")

# ==========================================
# ВКЛАДКА 2: ДНЕВНИК, ЧАСЫ И СОВЕТЫ ПО МЕНЮ
# ==========================================
with tab_day:
    st.subheader(f"Итоги дня и активность ({selected_person})")

    with st.container(border=True):
        st.markdown("⌚ **Данные с умных часов за сегодня**")
        col_w1, col_w2 = st.columns(2)
        with col_w1:
            steps = st.number_input("Пройдено шагов", min_value=0, step=500, value=today_log.get("steps", 6000))
        with col_w2:
            active_kcal = st.number_input("Активные ккал (если есть в часах)", min_value=0, step=50, value=today_log.get("active_kcal", 250))
        
        if steps != today_log["steps"] or active_kcal != today_log["active_kcal"]:
            today_log["steps"] = steps
            today_log["active_kcal"] = active_kcal
            save_data(data)

    target_k, target_p, target_f, target_c = calculate_targets(profile, steps, active_kcal)

    eaten_k = sum(m["kcal"] for m in today_log["meals"])
    eaten_p = sum(m["p"] for m in today_log["meals"])
    eaten_f = sum(m["f"] for m in today_log["meals"])
    eaten_c = sum(m["c"] for m in today_log["meals"])

    st.markdown("### 🎯 Норма на сегодня (с учетом нагрузки с часов)")
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Калории", f"{eaten_k} / {target_k}")
    m2.metric("Белки (г)", f"{round(eaten_p)} / {target_p}")
    m3.metric("Жиры (г)", f"{round(eaten_f)} / {target_f}")
    m4.metric("Углеводы (г)", f"{round(eaten_c)} / {target_c}")

    if today_log["meals"]:
        st.markdown("**Съедено за сегодня:**")
        for idx, m in enumerate(today_log["meals"]):
            st.write(f"{idx+1}. **{m['name']}** ({m['grams']} г) — {m['kcal']} ккал (Б:{m['p']}, Ж:{m['f']}, У:{m['c']})")
        if st.button("🗑 Очистить сегодняшний список"):
            today_log["meals"] = []
            save_data(data)
            st.rerun()
    else:
        st.caption("Сегодня еще нет записанных блюд.")

    st.divider()
    if st.button("✨ Проанализировать день и подсказать ужин / меню", type="primary"):
        if not API_KEY:
            st.error("Не настроен ключ API!")
        else:
            with st.spinner("Анализируем рацион и нагрузку с часов..."):
                advice_prompt = (
                    f"Проанализируй сегодняшний рацион для человека: {selected_person} "
                    f"(возраст {profile['age']}, рост {profile['height']} см, вес {profile['weight']} кг, цель: {profile['goal']}).\n"
                    f"Активность по часам сегодня: {steps} шагов, сожжено активных калорий: {active_kcal} ккал.\n"
                    f"Дневная норма с учетом часов: {target_k} ккал (Б:{target_p}г, Ж:{target_f}г, У:{target_c}г).\n"
                    f"Фактически съедено сейчас: {eaten_k} ккал (Б:{eaten_p}г, Ж:{eaten_f}г, У:{eaten_c}г).\n"
                    f"Список съеденных блюд: {json.dumps(today_log['meals'], ensure_ascii=False)}.\n\n"
                    "Напиши теплый, уважительный и очень понятный ответ (без сложной медицинской терминологии):\n"
                    "1. Похвали за активность по часам и объясни, как она повлияла на сегодняшний план питания.\n"
                    "2. Чего сейчас не хватает в рационе (например, белка или сложных углеводов) или с чем перебор.\n"
                    "3. Предложи 2-3 очень простых и привычных варианта на оставшийся прием пищи (или несложные замены на завтра), чтобы закрыть норму."
                )
                resp = client.models.generate_content(model="gemini-2.5-flash", contents=advice_prompt)
                st.markdown(resp.text)

# ==========================================
# ВКЛАДКА 3: НАСТРОЙКИ ПРОФИЛЯ
# ==========================================
with tab_profile:
    st.subheader(f"Параметры: {selected_person}")
    p_age = st.number_input("Возраст (лет)", min_value=18, max_value=100, value=profile["age"])
    p_height = st.number_input("Рост (см)", min_value=120, max_value=220, value=profile["height"])
    p_weight = st.number_input("Текущий вес (кг)", min_value=35.0, max_value=200.0, step=0.5, value=float(profile["weight"]))
    p_goal = st.selectbox(
        "Главная цель", 
        ["Плавно снизить вес", "Поддерживать форму и здоровье", "Набрать мышечную массу"],
        index=["Плавно снизить вес", "Поддерживать форму и здоровье", "Набрать мышечную массу"].index(profile["goal"])
    )

    if st.button("💾 Сохранить мои параметры"):
        profile["age"] = p_age
        profile["height"] = p_height
        profile["weight"] = p_weight
        profile["goal"] = p_goal
        save_data(data)
        st.success("Параметры успешно сохранены!")
