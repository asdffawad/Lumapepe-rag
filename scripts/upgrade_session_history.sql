-- =====================================================
-- RAG 用户历史会话列表增量迁移
-- 文件: upgrade_session_history.sql
-- 目标: MySQL 8.x / lumapepe
-- 说明:
--   1. 幂等执行，重复运行不会重复添加字段或索引。
--   2. 只新增字段和索引，不删除旧字段，不修改已有字段类型。
--   3. 旧数据即使未回填，也会通过 ragz_messages 子查询展示历史列表。
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

CALL add_column_if_missing('ragz_sessions', 'title', '`title` VARCHAR(255) NULL COMMENT ''会话标题''');
CALL add_column_if_missing('ragz_sessions', 'last_message_content', '`last_message_content` VARCHAR(500) NULL COMMENT ''最近一条消息''');
CALL add_column_if_missing('ragz_sessions', 'last_message_role', '`last_message_role` VARCHAR(32) NULL COMMENT ''最近消息角色''');
CALL add_column_if_missing('ragz_sessions', 'last_message_at', '`last_message_at` DATETIME NULL COMMENT ''最近消息时间''');
CALL add_column_if_missing('ragz_sessions', 'message_count', '`message_count` INT NOT NULL DEFAULT 0 COMMENT ''消息数量''');
CALL add_column_if_missing('ragz_sessions', 'pinned', '`pinned` TINYINT NOT NULL DEFAULT 0 COMMENT ''是否置顶''');
CALL add_column_if_missing('ragz_sessions', 'archived', '`archived` TINYINT NOT NULL DEFAULT 0 COMMENT ''是否归档''');

CALL add_index_if_missing('ragz_sessions', 'idx_ragz_sessions_user_history', '(`user_id`, `last_message_at`, `updated_at`)');
CALL add_index_if_missing('ragz_sessions', 'idx_ragz_sessions_member_history', '(`member_user_id`, `last_message_at`, `updated_at`)');

DROP PROCEDURE IF EXISTS add_column_if_missing;
DROP PROCEDURE IF EXISTS add_index_if_missing;

SELECT 'upgrade_session_history migration completed' AS status;
