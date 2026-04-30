-- =====================================================
-- RAG 转人工客服 MVP 数据库迁移脚本
-- 文件: upgrade_kefu_handoff.sql
-- 目标: MySQL 8.x / lumapepe
-- 说明:
--   1. 幂等执行，重复运行不会重复添加字段或索引。
--   2. 只新增字段和索引，不删除字段，不修改已有字段类型，不添加强外键。
--   3. promotion_kefu_message.content 仍保留原 varchar(2048)，客服详情读取 ragz_sessions.metadata。
-- =====================================================

USE lumapepe;

DELIMITER //

DROP PROCEDURE IF EXISTS add_column_if_missing//
CREATE PROCEDURE add_column_if_missing(
    IN p_table_name VARCHAR(64),
    IN p_column_name VARCHAR(64),
    IN p_column_definition TEXT
)
BEGIN
    IF EXISTS (
        SELECT 1
        FROM INFORMATION_SCHEMA.TABLES
        WHERE TABLE_SCHEMA = DATABASE()
          AND TABLE_NAME = p_table_name
    ) AND NOT EXISTS (
        SELECT 1
        FROM INFORMATION_SCHEMA.COLUMNS
        WHERE TABLE_SCHEMA = DATABASE()
          AND TABLE_NAME = p_table_name
          AND COLUMN_NAME = p_column_name
    ) THEN
        SET @sql = CONCAT('ALTER TABLE `', p_table_name, '` ADD COLUMN ', p_column_definition);
        PREPARE stmt FROM @sql;
        EXECUTE stmt;
        DEALLOCATE PREPARE stmt;
    END IF;
END//

DROP PROCEDURE IF EXISTS add_index_if_missing//
CREATE PROCEDURE add_index_if_missing(
    IN p_table_name VARCHAR(64),
    IN p_index_name VARCHAR(64),
    IN p_index_definition TEXT
)
BEGIN
    IF EXISTS (
        SELECT 1
        FROM INFORMATION_SCHEMA.TABLES
        WHERE TABLE_SCHEMA = DATABASE()
          AND TABLE_NAME = p_table_name
    ) AND NOT EXISTS (
        SELECT 1
        FROM INFORMATION_SCHEMA.STATISTICS
        WHERE TABLE_SCHEMA = DATABASE()
          AND TABLE_NAME = p_table_name
          AND INDEX_NAME = p_index_name
    ) THEN
        SET @sql = CONCAT('ALTER TABLE `', p_table_name, '` ADD INDEX `', p_index_name, '` ', p_index_definition);
        PREPARE stmt FROM @sql;
        EXECUTE stmt;
        DEALLOCATE PREPARE stmt;
    END IF;
END//

DELIMITER ;

-- ragz_sessions: RAG 会话与会员/客服会话软关联
CALL add_column_if_missing('ragz_sessions', 'member_user_id', '`member_user_id` BIGINT NULL COMMENT ''关联 member_user.id''');
CALL add_column_if_missing('ragz_sessions', 'kefu_conversation_id', '`kefu_conversation_id` BIGINT NULL COMMENT ''关联 promotion_kefu_conversation.id''');
CALL add_column_if_missing('ragz_sessions', 'tenant_id', '`tenant_id` BIGINT NOT NULL DEFAULT 0 COMMENT ''租户编号''');
CALL add_column_if_missing('ragz_sessions', 'handoff_status', '`handoff_status` TINYINT NOT NULL DEFAULT 0 COMMENT ''0-AI中 1-待人工 2-人工中 3-已关闭''');
CALL add_column_if_missing('ragz_sessions', 'handoff_at', '`handoff_at` DATETIME NULL COMMENT ''转人工时间''');

CALL add_index_if_missing('ragz_sessions', 'idx_ragz_sessions_member', '(`tenant_id`, `member_user_id`, `status`)');
CALL add_index_if_missing('ragz_sessions', 'idx_ragz_sessions_kefu_conv', '(`kefu_conversation_id`)');
CALL add_index_if_missing('ragz_sessions', 'idx_ragz_sessions_handoff', '(`handoff_status`, `handoff_at`)');

-- ragz_messages: RAG 消息与客服消息软关联
CALL add_column_if_missing('ragz_messages', 'member_user_id', '`member_user_id` BIGINT NULL COMMENT ''关联 member_user.id''');
CALL add_column_if_missing('ragz_messages', 'kefu_conversation_id', '`kefu_conversation_id` BIGINT NULL COMMENT ''关联 promotion_kefu_conversation.id''');
CALL add_column_if_missing('ragz_messages', 'kefu_message_id', '`kefu_message_id` BIGINT NULL COMMENT ''镜像到客服消息后的 promotion_kefu_message.id''');
CALL add_column_if_missing('ragz_messages', 'sender_type', '`sender_type` TINYINT NULL COMMENT ''1-用户 2-客服 3-AI 4-系统''');
CALL add_column_if_missing('ragz_messages', 'tenant_id', '`tenant_id` BIGINT NOT NULL DEFAULT 0 COMMENT ''租户编号''');

CALL add_index_if_missing('ragz_messages', 'idx_ragz_messages_conv_time', '(`kefu_conversation_id`, `created_at`)');
CALL add_index_if_missing('ragz_messages', 'idx_ragz_messages_kefu_msg', '(`kefu_message_id`)');
CALL add_index_if_missing('ragz_messages', 'idx_ragz_messages_member_time', '(`tenant_id`, `member_user_id`, `created_at`)');

-- ragz_documents: 多租户预留
CALL add_column_if_missing('ragz_documents', 'tenant_id', '`tenant_id` BIGINT NOT NULL DEFAULT 0 COMMENT ''租户编号''');
CALL add_index_if_missing('ragz_documents', 'idx_ragz_documents_tenant_type', '(`tenant_id`, `doc_type`, `status`)');

-- promotion_kefu_message: 会话消息查询索引
CALL add_index_if_missing('promotion_kefu_message', 'idx_kefu_msg_conv_time', '(`conversation_id`, `create_time`)');
CALL add_index_if_missing('promotion_kefu_message', 'idx_kefu_msg_sender_time', '(`sender_type`, `sender_id`, `create_time`)');

DROP PROCEDURE IF EXISTS add_column_if_missing;
DROP PROCEDURE IF EXISTS add_index_if_missing;

SELECT 'upgrade_kefu_handoff migration completed' AS status;
