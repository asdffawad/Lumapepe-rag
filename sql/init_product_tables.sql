-- ============================================================
-- 商品SKU解析与检索模块 - 数据库初始化脚本
-- ============================================================
-- 执行前请确认已创建数据库: CREATE DATABASE lumapepe CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
-- ============================================================

-- ----------------------------------------------------------
-- 1. SPU表（商品主表）
-- ----------------------------------------------------------
CREATE TABLE IF NOT EXISTS product_spu (
  `id` bigint(20) NOT NULL AUTO_INCREMENT COMMENT '商品 SPU 编号，自增',
  `shop_id` bigint(20) DEFAULT NULL COMMENT '店铺编号，关联 mall_shop.id',
  `name` varchar(512) COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '商品名称',
  `keyword` varchar(256) COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '关键字',
  `introduction` varchar(5000) COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '商品简介',
  `description` text COLLATE utf8mb4_unicode_ci COMMENT '商品详情',
  `category_id` bigint(20) DEFAULT NULL COMMENT '商品分类编号',
  `brand_id` int(11) DEFAULT NULL COMMENT '商品品牌编号',
  `pic_url` varchar(256) COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '商品封面图',
  `slider_pic_urls` longtext COLLATE utf8mb4_unicode_ci COMMENT '商品轮播图地址\n 数组，以逗号分隔\n 最多上传15张',
  `sort` int(11) NOT NULL DEFAULT '0' COMMENT '排序字段',
  `status` tinyint(4) NOT NULL COMMENT '商品状态: 1 上架（开启） 0 下架（禁用） -1 回收 -2草稿箱',
  `spec_type` bit(1) DEFAULT NULL COMMENT '规格类型：0 单规格 1 多规格',
  `price` decimal(20,2) DEFAULT NULL COMMENT '商品价格，单位：美元',
  `market_price` decimal(20,2) DEFAULT NULL COMMENT '市场价，单位：美元',
  `cost_price` decimal(20,2) DEFAULT NULL COMMENT '成本价，单位：美元',
  `stock` int(11) NOT NULL DEFAULT '0' COMMENT '库存',
  `delivery_types` varchar(32) COLLATE utf8mb4_unicode_ci DEFAULT '' COMMENT '配送方式数组',
  `delivery_template_id` bigint(20) DEFAULT NULL COMMENT '物流配置模板编号',
  `give_integral` int(11) NOT NULL DEFAULT '0' COMMENT '赠送积分',
  `sub_commission_type` bit(1) DEFAULT NULL COMMENT '分销类型',
  `sales_count` int(11) DEFAULT '0' COMMENT '商品销量',
  `virtual_sales_count` int(11) DEFAULT '0' COMMENT '虚拟销量',
  `browse_count` int(11) DEFAULT '0' COMMENT '商品点击量',
  `pricing_source` varchar(16) COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT 'CATEGORY' COMMENT '定价来源：CATEGORY-分类 PRODUCT-商品覆盖',
  `fee_rate` decimal(8,4) DEFAULT NULL COMMENT '商品级手续费率（百分比，NULL=跟随分类）',
  `tax_rate` decimal(8,4) DEFAULT NULL COMMENT '商品级税金率（百分比，NULL=跟随分类）',
  `profit_rate` decimal(8,4) DEFAULT NULL COMMENT '商品级毛利率（百分比，NULL=跟随分类）',
  `discount_rate` decimal(8,4) DEFAULT NULL COMMENT '商品级折扣率（百分比，NULL=跟随分类）',
  `shipping_fee` decimal(19,2) DEFAULT NULL COMMENT '商品级运费（单位：美元 USD，NULL=跟随分类）',
  `pricing_updated_time` datetime DEFAULT NULL COMMENT '定价参数最近更新时间',
  `create_time` datetime DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `update_time` datetime DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  `creator` varchar(64) COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '创建人',
  `updater` varchar(64) COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '更新人',
  `deleted` bit(1) NOT NULL DEFAULT b'0' COMMENT '是否删除',
  `tenant_id` bigint(20) NOT NULL DEFAULT '0' COMMENT '租户编号',
  `video_url` varchar(500) COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '商品视频地址',
  `specs` json DEFAULT NULL COMMENT 'SPU 级别规格定义（status=0平台，1自定义）',
  `detail_pic_urls` varchar(2000) COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '商品详情图地址\n 数组，以逗号分隔\n 最多上传15张',
  `supply_chain_code` varchar(32) COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '供应链编码：gigab2b/cj，空=非供应链导入',
  `supply_external_sku` varchar(512) COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '供应链侧 SKU/PID 摘要，多规格逗号拼接',
  PRIMARY KEY (`id`) USING BTREE,
  KEY `idx_shop_id` (`shop_id`) USING BTREE COMMENT '商品-店铺编号索引',
  KEY `idx_product_spu_shop_status` (`shop_id`,`status`) USING BTREE,
  KEY `idx_product_spu_shop_create` (`shop_id`,`create_time`) USING BTREE,
  KEY `idx_pricing_source` (`pricing_source`),
  KEY `idx_category_pricing` (`category_id`,`pricing_source`)
) ENGINE=InnoDB AUTO_INCREMENT=839 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci ROW_FORMAT=DYNAMIC COMMENT='商品spu';

-- ----------------------------------------------------------
-- 2. SKU表（商品规格表）
-- ----------------------------------------------------------
CREATE TABLE IF NOT EXISTS product_sku (
  `id` bigint(20) NOT NULL AUTO_INCREMENT COMMENT '主键',
  `spu_id` bigint(20) NOT NULL COMMENT 'spu编号',
  `properties` varchar(2500) COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '属性数组，JSON 格式 [{propertId: , valueId: }, {propertId: , valueId: }]',
  `price` decimal(20,2) DEFAULT NULL COMMENT '商品价格，单位：美元',
  `market_price` decimal(20,2) DEFAULT NULL COMMENT '市场价，单位：美元',
  `cost_price` decimal(20,2) DEFAULT NULL COMMENT '成本价，单位：美元',
  `suggest_sell_price` decimal(12,2) DEFAULT NULL COMMENT '建议售价（美元），如 CJ variantSugSellPrice',
  `bar_code` varchar(64) COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT 'SKU 的条形码',
  `pic_url` varchar(256) COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '图片地址',
  `stock` int(11) DEFAULT NULL COMMENT '库存',
  `weight` double DEFAULT NULL COMMENT '商品重量，单位：kg 千克',
  `volume` double DEFAULT NULL COMMENT '商品体积，单位：m^3 平米',
  `first_brokerage_price` int(11) DEFAULT NULL COMMENT '一级分销的佣金，单位：分',
  `second_brokerage_price` int(11) DEFAULT NULL COMMENT '二级分销的佣金，单位：分',
  `sales_count` int(11) DEFAULT NULL COMMENT '商品销量',
  `create_time` datetime DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `update_time` datetime DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  `creator` varchar(64) COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '创建人',
  `updater` double(64,0) DEFAULT NULL COMMENT '更新人',
  `deleted` bit(1) NOT NULL DEFAULT b'0' COMMENT '是否删除',
  `tenant_id` bigint(20) NOT NULL DEFAULT '0' COMMENT '租户编号',
  `supply_chain_code` varchar(32) COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '供应链编码：gigab2b/cj',
  `supply_external_sku` varchar(128) COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '供应链侧 SKU 编号（Giga item code / CJ variant sku 等）',
  PRIMARY KEY (`id`) USING BTREE,
  KEY `idx_spu_id` (`spu_id`) USING BTREE,
  KEY `idx_bar_code` (`bar_code`) USING BTREE
) ENGINE=InnoDB AUTO_INCREMENT=347 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci ROW_FORMAT=DYNAMIC COMMENT='商品sku';

-- ----------------------------------------------------------
-- 3. Alias表（商品别名表）
-- ----------------------------------------------------------
CREATE TABLE IF NOT EXISTS product_alias (
    id bigint(20) NOT NULL AUTO_INCREMENT COMMENT '别名ID',
    spu_id bigint(20) NOT NULL COMMENT '关联的SPU ID',
    alias_name varchar(255) COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '别名原文',
    alias_normalized varchar(255) COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '标准化后的别名',
    create_time datetime DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    update_time datetime DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
    deleted bit(1) NOT NULL DEFAULT b'0' COMMENT '是否删除',
    tenant_id bigint(20) NOT NULL DEFAULT '0' COMMENT '租户编号',
    PRIMARY KEY (`id`) USING BTREE,
    KEY idx_alias_normalized (alias_normalized) USING BTREE,
    KEY idx_spu_id (spu_id) USING BTREE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='商品别名表';

-- ----------------------------------------------------------
-- 4. 测试数据
-- ----------------------------------------------------------
-- 插入测试SPU数据（price 单位：分）
INSERT INTO product_spu (id, shop_id, name, keyword, introduction, description, category_id, brand_id, pic_url, sort, status, spec_type, price, market_price, cost_price, stock, deleted, tenant_id) VALUES
(1, 1, 'iPhone 16 Pro', '苹果,iPhone,旗舰手机,5G', 'Apple最新旗舰智能手机', '搭载A18 Pro芯片，支持5G网络，配备专业级摄像头系统', 1, 1, 'https://example.com/iphone16-pro.jpg', 0, 1, b'0', 7999.00, 8999.00, 6999.00, 500, b'0', 1),
(2, 1, 'MacBook Pro 16寸', '苹果,笔记本,MacBook,办公', 'Apple专业级笔记本电脑', 'M3 Max芯片，36GB内存，1TB SSD', 2, 1, 'https://example.com/macbook-pro-16.jpg', 0, 1, b'0', 2499.00, 2699.00, 2299.00, 200, b'0', 1),
(3, 1, 'AirPods Pro', '苹果,耳机,蓝牙耳机,降噪', 'Apple主动降噪无线耳机', '支持主动降噪，空间音频，MagSafe充电盒', 3, 1, 'https://example.com/airpods-pro.jpg', 0, 1, b'0', 189.00, 219.00, 159.00, 1000, b'0', 1);

-- 插入测试SKU数据（price 单位：美元）
INSERT INTO product_sku (id, spu_id, properties, price, market_price, cost_price, suggest_sell_price, bar_code, pic_url, stock, deleted, tenant_id) VALUES
(1, 1, '[{"propertyId":1,"valueId":1},{"propertyId":2,"valueId":3}]', 7999.00, 8999.00, 6999.00, 7499.00, '6901234567890', 'https://example.com/iphone16-pro-black.jpg', 200, b'0', 1),
(2, 1, '[{"propertyId":1,"valueId":1},{"propertyId":2,"valueId":4}]', 8999.00, 9999.00, 7999.00, 8499.00, '6901234567891', 'https://example.com/iphone16-pro-black-512.jpg', 150, b'0', 1),
(3, 1, '[{"propertyId":1,"valueId":2},{"propertyId":2,"valueId":3}]', 7999.00, 8999.00, 6999.00, 7499.00, '6901234567892', 'https://example.com/iphone16-pro-white.jpg', 150, b'0', 1),
(4, 2, '[{"propertyId":1,"valueId":5},{"propertyId":2,"valueId":6}]', 2499.00, 2699.00, 2299.00, 2399.00, 'ABC1234567890', 'https://example.com/macbook-pro-16.jpg', 50, b'0', 1),
(5, 3, '[{"propertyId":1,"valueId":2},{"propertyId":2,"valueId":7}]', 189.00, 219.00, 159.00, 179.00, 'ABC1234567891', 'https://example.com/airpods-pro.jpg', 500, b'0', 1);

-- 插入测试别名数据
INSERT INTO product_alias (spu_id, alias_name, alias_normalized, deleted, tenant_id) VALUES
(1, '苹果手机', '苹果手机', b'0', 1),
(1, 'iPhone', 'iphone', b'0', 1),
(1, '水果机', '水果机', b'0', 1),
(2, 'MacBook', 'macbook', b'0', 1),
(2, '苹果笔记本', '苹果笔记本', b'0', 1),
(3, 'AirPods', 'airpods', b'0', 1),
(3, '苹果耳机', '苹果耳机', b'0', 1);

-- ----------------------------------------------------------
-- 5. ETL字典构建（从数据库拉取数据构建JSON）
-- ----------------------------------------------------------
-- 运行命令：python -m app.etl --output data/product_dict.json --pretty
-- ----------------------------------------------------------

-- ----------------------------------------------------------
-- 6. 维护存储过程（可选）
-- ----------------------------------------------------------
-- 别名同步存储过程：当SPU名称更新时，自动同步别名
DELIMITER //
CREATE PROCEDURE IF NOT EXISTS sync_spu_aliases(IN p_spu_id bigint(20))
BEGIN
    DECLARE v_name varchar(512);
    SELECT name INTO v_name FROM product_spu WHERE id = p_spu_id;
    INSERT INTO product_alias (spu_id, alias_name, alias_normalized)
    VALUES (p_spu_id, v_name, v_name)
    ON DUPLICATE KEY UPDATE alias_name = v_name;
END //
DELIMITER ;