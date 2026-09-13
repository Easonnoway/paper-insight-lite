-- 每个分类一个展示颜色；键值与 backend/paper_categories.py 的 CATEGORY_COLOR_PALETTE 保持同序。
ALTER TABLE paper_categories ADD COLUMN IF NOT EXISTS color TEXT;

-- 对存量行按 id 的 md5 确定性回填；WHERE 守卫保证重跑不会覆盖用户手选的颜色。
UPDATE paper_categories
SET color = (ARRAY['amber','blue','violet','emerald','rose','teal','orange','cyan','pink','slate'])[
    1 + (('x' || substr(md5(id::text), 1, 8))::bit(32)::bigint % 10)::int]
WHERE color IS NULL;
