-- 店铺与买家档案，以及订单结算时的配送费和付款方式。
CREATE TABLE IF NOT EXISTS public.mall_shop_profiles (
    shop_id TEXT PRIMARY KEY REFERENCES public.mall_shops,
    category TEXT NOT NULL,
    city TEXT NOT NULL,
    joined_on DATE NOT NULL,
    description TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS public.mall_buyer_profiles (
    buyer_id TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    city TEXT NOT NULL,
    joined_on DATE NOT NULL
);
CREATE TABLE IF NOT EXISTS public.mall_order_checkout (
    order_id TEXT PRIMARY KEY REFERENCES public.mall_orders,
    shipping NUMERIC(9,2) NOT NULL CHECK (shipping >= 0),
    payment_method TEXT NOT NULL
);
