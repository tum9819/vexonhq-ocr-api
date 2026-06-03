import pytest
from phase3a_ai_categorize_routes import _build_cashflow_prompt

def test_build_cashflow_prompt_contains_real_codes():
    categories = [
        {"code": "food_raw", "name_th": "วัตถุดิบอาหาร", "parent_code": "food_cost"},
        {"code": "cleaning", "name_th": "ค่าทำความสะอาด", "parent_code": "misc"},
        {"code": "musician_fee", "name_th": "ค่านักดนตรี", "parent_code": "personnel"},
        {"code": "transfer_error", "name_th": "โอนผิด/คืนเงิน", "parent_code": None},
    ]
    prompt = _build_cashflow_prompt("ผักสดหมูสไลด์", categories)
    
    # Assert real codes are mentioned in the examples
    assert "food_raw" in prompt
    assert "cleaning" in prompt
    assert "musician_fee" in prompt
    assert "transfer_error" in prompt
    
    # Assert phantom codes are NOT in the prompt
    assert "raw_food" not in prompt
    assert "supplies" not in prompt
    assert "entertainment" not in prompt
    assert "customer_refund" not in prompt
