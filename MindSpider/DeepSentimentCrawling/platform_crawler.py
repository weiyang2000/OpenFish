#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
DeepSentimentCrawling模块 - 平台爬虫管理器
负责配置和调用MediaCrawler进行多平台爬取
"""

import os
import sys
import subprocess
import threading
from datetime import date, datetime
from pathlib import Path
from typing import Callable, List, Dict, Optional
import json
from loguru import logger

# 添加项目根目录到路径
project_root = Path(__file__).parent.parent
repo_root = project_root.parent
sys.path.append(str(project_root))
sys.path.append(str(repo_root))

try:
    import config
except ImportError:
    raise ImportError("无法导入config.py配置文件")

from utils.runtime_database import ensure_crawler_database_schema, load_runtime_database_config


PLATFORM_RECORD_TABLES = {
    "xhs": ("xhs_note", "xhs_note_comment"),
    "dy": ("douyin_aweme", "douyin_aweme_comment"),
    "ks": ("kuaishou_video", "kuaishou_video_comment"),
    "bili": ("bilibili_video", "bilibili_video_comment"),
    "wb": ("weibo_note", "weibo_note_comment"),
    "tieba": ("tieba_note", "tieba_comment"),
    "zhihu": ("zhihu_content", "zhihu_comment"),
}

CRAWLER_SOFT_FAILURE_MARKERS = (
    "have not found qrcode",
    "login failed",
    "page.wait_for_selector: timeout",
    "traceback (most recent call last)",
    "登录失败",
)

COOKIE_LOGIN_FAILURE_MARKERS = (
    "cookie may be invalid",
)

CrawlerLogCallback = Callable[[str, str], None]


class _temporary_environ:
    def __init__(self, values: Dict[str, str]) -> None:
        self.values = values
        self.previous: Dict[str, str | None] = {}

    def __enter__(self):
        for key, value in self.values.items():
            self.previous[key] = os.environ.get(key)
            os.environ[key] = value
        return self

    def __exit__(self, exc_type, exc, tb):
        for key, value in self.previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


class PlatformCrawler:
    """平台爬虫管理器"""
    
    def __init__(self, log_callback: Optional[CrawlerLogCallback] = None):
        """初始化平台爬虫管理器"""
        self.mediacrawler_path = Path(__file__).parent / "MediaCrawler"
        self.repo_root = Path(__file__).resolve().parents[2]
        self.supported_platforms = ['xhs', 'dy', 'ks', 'bili', 'wb', 'tieba', 'zhihu']
        self.crawl_stats = {}
        self._schema_initialized: set[str] = set()
        self.log_callback = log_callback
        
        # 确保MediaCrawler子模块已初始化
        db_config_path = self.mediacrawler_path / "config" / "db_config.py"
        if not self.mediacrawler_path.exists() or not db_config_path.exists():
            logger.error("MediaCrawler子模块未初始化或不完整")
            logger.error("请在项目根目录运行以下命令初始化子模块:")
            logger.error("   git submodule update --init --recursive")
            raise FileNotFoundError("MediaCrawler子模块未初始化，请先运行: git submodule update --init --recursive")

        logger.info(f"初始化平台爬虫管理器，MediaCrawler路径: {self.mediacrawler_path}")
    
    def configure_mediacrawler_db(self):
        """配置MediaCrawler使用根目录 .env 中的数据库配置。"""
        try:
            if hasattr(config, "reload_settings"):
                config.reload_settings()
            runtime_db = load_runtime_database_config(config.settings)
            runtime_db.require_configured()
            runtime_db.apply_to_environment()
            logger.info(
                "已配置MediaCrawler使用统一数据库配置: "
                f"{runtime_db.dialect}://{runtime_db.host}:{runtime_db.port}/{runtime_db.name}"
            )
            return True
        except Exception as e:
            logger.exception(f"配置MediaCrawler数据库失败: {e}")
            return False
    
    def create_base_config(self, platform: str, keywords: List[str], 
                          crawler_type: str = "search", max_notes: int = 50) -> bool:
        """
        创建MediaCrawler的基础配置
        
        Args:
            platform: 平台名称
            keywords: 关键词列表
            crawler_type: 爬取类型
            max_notes: 最大爬取数量
        
        Returns:
            是否配置成功
        """
        try:
            if hasattr(config, "reload_settings"):
                config.reload_settings()
            save_data_option = load_runtime_database_config(config.settings).save_data_option

            base_config_path = self.mediacrawler_path / "config" / "base_config.py"
            
            # 将关键词列表转换为逗号分隔的字符串
            keywords_str = ",".join(keywords)
            
            # 读取原始配置文件
            with open(base_config_path, 'r', encoding='utf-8') as f:
                content = f.read()
            
            # 修改关键配置项
            # skip_until_paren: 当原始行是多行赋值（以"("结尾）被替换为单行后，
            # 需要跳过后续续行直到遇到配对的")"
            lines = content.split('\n')
            new_lines = []
            skip_until_paren = False

            for line in lines:
                # 跳过多行赋值的续行
                if skip_until_paren:
                    if line.strip() == ')':
                        skip_until_paren = False
                    continue

                replaced = None
                if line.startswith('PLATFORM = '):
                    replaced = f'PLATFORM = "{platform}"  # 平台，xhs | dy | ks | bili | wb | tieba | zhihu'
                elif line.startswith('KEYWORDS = '):
                    replaced = f'KEYWORDS = "{keywords_str}"  # 关键词搜索配置，以英文逗号分隔'
                elif line.startswith('CRAWLER_TYPE = '):
                    replaced = f'CRAWLER_TYPE = "{crawler_type}"  # 爬取类型，search(关键词搜索) | detail(帖子详情)| creator(创作者主页数据)'
                elif line.startswith('SAVE_DATA_OPTION = '):
                    replaced = f'SAVE_DATA_OPTION = "{save_data_option}"  # csv or db or json or sqlite or postgres'
                elif line.startswith('CRAWLER_MAX_NOTES_COUNT = '):
                    replaced = f'CRAWLER_MAX_NOTES_COUNT = {max_notes}'
                elif line.startswith('ENABLE_GET_COMMENTS = '):
                    replaced = 'ENABLE_GET_COMMENTS = True'
                elif line.startswith('CRAWLER_MAX_COMMENTS_COUNT_SINGLENOTES = '):
                    replaced = 'CRAWLER_MAX_COMMENTS_COUNT_SINGLENOTES = 20'
                elif line.startswith('HEADLESS = '):
                    replaced = 'HEADLESS = True'

                if replaced is not None:
                    new_lines.append(replaced)
                    # 若原始行是多行赋值开头（以"("结尾），跳过后续续行
                    if line.rstrip().endswith('('):
                        skip_until_paren = True
                else:
                    new_lines.append(line)
            
            # 写入新配置
            with open(base_config_path, 'w', encoding='utf-8') as f:
                f.write('\n'.join(new_lines))
            
            logger.info(f"已配置 {platform} 平台，爬取类型: {crawler_type}，关键词数量: {len(keywords)}，最大爬取数量: {max_notes}，保存数据方式: {save_data_option}")
            return True
            
        except Exception as e:
            logger.exception(f"创建基础配置失败: {e}")
            return False
    
    def run_crawler(
        self,
        platform: str,
        keywords: List[str],
        login_type: str = "qrcode",
        max_notes: int = 50,
        headless: bool = True,
        start_date: str | date | None = None,
        end_date: str | date | None = None,
    ) -> Dict:
        """
        运行爬虫
        
        Args:
            platform: 平台名称
            keywords: 关键词列表
            login_type: 登录方式
            max_notes: 最大爬取数量
        
        Returns:
            爬取结果统计
        """
        if platform not in self.supported_platforms:
            raise ValueError(f"不支持的平台: {platform}")
        
        if not keywords:
            raise ValueError("关键词列表不能为空")
        
        start_message = f"\n开始爬取平台: {platform}"
        start_message += f"\n关键词: {keywords[:5]}{'...' if len(keywords) > 5 else ''} (共{len(keywords)}个)"
        logger.info(start_message)
        
        start_time = datetime.now()
        touched_since_ms = int(start_time.timestamp() * 1000) - 5000
        
        try:
            # 配置数据库
            if not self.configure_mediacrawler_db():
                return {"success": False, "error": "数据库配置失败"}
            
            # 创建基础配置
            if not self.create_base_config(platform, keywords, "search", max_notes):
                return {"success": False, "error": "基础配置创建失败"}
            
            save_data_option = load_runtime_database_config(config.settings).save_data_option
            if not self._ensure_mediacrawler_schema(save_data_option):
                return {"success": False, "error": f"{save_data_option} 数据库表初始化失败", "platform": platform}

            # 构建命令
            cmd = [
                sys.executable, "main.py",
                "--platform", platform,
                "--lt", login_type,
                "--type", "search",
                "--save_data_option", save_data_option,
                "--headless", "true" if headless else "false"
            ]
            
            logger.info(f"执行命令: {' '.join(cmd)}")
            before_counts = self._count_platform_records(platform)
            date_filter_env = self._date_filter_env(start_date, end_date)
            with _temporary_environ(date_filter_env):
                result = self._run_media_crawler_command(cmd, timeout=3600)
            
            end_time = datetime.now()
            duration = (end_time - start_time).total_seconds()
            output_stats = self._parse_crawl_output(
                (result.stdout or "").splitlines(),
                (result.stderr or "").splitlines(),
            )
            detected_error = self._detect_soft_failure(
                result.stdout or "",
                result.stderr or "",
                login_type,
            )
            success = result.returncode == 0 and detected_error is None
            after_counts = self._count_platform_records(platform)
            notes_count = max(
                output_stats.get("notes_count", 0),
                after_counts.get("notes", 0) - before_counts.get("notes", 0),
            )
            comments_count = max(
                output_stats.get("comments_count", 0),
                after_counts.get("comments", 0) - before_counts.get("comments", 0),
            )
            
            # 创建统计信息
            crawl_stats = {
                "platform": platform,
                "keywords_count": len(keywords),
                "duration_seconds": duration,
                "start_time": start_time.isoformat(),
                "end_time": end_time.isoformat(),
                "return_code": result.returncode,
                "success": success,
                "notes_count": notes_count,
                "comments_count": comments_count,
                "errors_count": output_stats.get("errors_count", 0),
            }
            if detected_error:
                crawl_stats["error"] = detected_error
            elif result.returncode != 0:
                crawl_stats["error"] = f"MediaCrawler subprocess exited with return code {result.returncode}"
            elif success:
                crawl_stats["sentiment"] = self._postprocess_sentiment(
                    platform,
                    start_date=start_date,
                    end_date=end_date,
                    touched_since_ms=touched_since_ms,
                )
            
            # 保存统计信息
            self.crawl_stats[platform] = crawl_stats
            
            if success:
                logger.info(
                    f"✅ {platform} 爬取完成，耗时: {duration:.1f}秒，"
                    f"新增内容: {notes_count}，新增评论: {comments_count}"
                )
            else:
                logger.error(
                    f"❌ {platform} 爬取失败，返回码: {result.returncode}，"
                    f"原因: {crawl_stats.get('error', '未知错误')}"
                )
            
            return crawl_stats
            
        except subprocess.TimeoutExpired:
            logger.exception(f"❌ {platform} 爬取超时")
            return {"success": False, "error": "爬取超时", "platform": platform}
        except Exception as e:
            logger.exception(f"❌ {platform} 爬取异常: {e}")
            return {"success": False, "error": str(e), "platform": platform}

    def _postprocess_sentiment(
        self,
        platform: str,
        *,
        start_date: str | date | None = None,
        end_date: str | date | None = None,
        touched_since_ms: int | None = None,
    ) -> Dict:
        logger.info(f"开始执行 {platform} 爬虫情绪后处理")
        from MindSpider.DeepSentimentCrawling.sentiment_postprocessor import (
            run_crawler_sentiment_postprocessing,
        )

        stats = run_crawler_sentiment_postprocessing(
            platform,
            start_date=start_date,
            end_date=end_date,
            touched_since_ms=touched_since_ms,
        )
        if stats.get("error"):
            logger.warning(f"{platform} 情绪后处理未完成: {stats['error']}")
        elif stats.get("disabled"):
            logger.info(f"{platform} 情绪后处理已禁用")
        else:
            logger.info(
                f"{platform} 情绪后处理完成，处理: {stats.get('processed', 0)}，"
                f"写回: {stats.get('updated', 0)}，失败: {stats.get('failed', 0)}"
            )
        return stats

    @staticmethod
    def _date_filter_env(
        start_date: str | date | None,
        end_date: str | date | None,
    ) -> Dict[str, str]:
        env: Dict[str, str] = {}
        if start_date and end_date:
            env["CRAWLER_START_DATE"] = str(start_date)[:10]
            env["CRAWLER_END_DATE"] = str(end_date)[:10]
        return env

    def _ensure_mediacrawler_schema(self, save_data_option: str) -> bool:
        init_db_type = {
            "postgres": "postgres",
            "db": "mysql",
            "sqlite": "sqlite",
        }.get(save_data_option)
        if not init_db_type or init_db_type in self._schema_initialized:
            return True

        try:
            logger.info(f"初始化统一爬虫数据库表: {init_db_type}")
            ensure_crawler_database_schema(self.repo_root, config.settings, timeout_seconds=180)
        except subprocess.TimeoutExpired:
            logger.exception(f"❌ 初始化 MediaCrawler {init_db_type} 数据库表超时")
            return False
        except Exception as exc:
            logger.exception(f"❌ 初始化 MediaCrawler {init_db_type} 数据库表失败: {exc}")
            return False

        self._schema_initialized.add(init_db_type)
        return True
    
    def _parse_crawl_output(self, output_lines: List[str], error_lines: List[str]) -> Dict:
        """解析爬取输出，提取统计信息"""
        stats = {
            "notes_count": 0,
            "comments_count": 0,
            "errors_count": 0,
            "login_required": False
        }
        
        # 解析输出行
        for line in output_lines:
            if "条笔记" in line or "条内容" in line:
                try:
                    # 提取数字
                    import re
                    numbers = re.findall(r'\d+', line)
                    if numbers:
                        stats["notes_count"] = int(numbers[0])
                except:
                    pass
            elif "条评论" in line:
                try:
                    import re
                    numbers = re.findall(r'\d+', line)
                    if numbers:
                        stats["comments_count"] = int(numbers[0])
                except:
                    pass
            elif "登录" in line or "扫码" in line:
                stats["login_required"] = True
        
        # 解析错误行
        for line in error_lines:
            if "error" in line.lower() or "异常" in line:
                stats["errors_count"] += 1
        
        return stats

    @staticmethod
    def _detect_soft_failure(stdout: str, stderr: str, login_type: str = "") -> Optional[str]:
        output = f"{stdout}\n{stderr}".lower()
        markers = CRAWLER_SOFT_FAILURE_MARKERS
        if login_type == "cookie":
            markers = (*COOKIE_LOGIN_FAILURE_MARKERS, *markers)
        for marker in markers:
            if marker in output:
                return f"MediaCrawler reported failure: {marker}"
        return None

    def _run_media_crawler_command(self, cmd: List[str], timeout: int) -> subprocess.CompletedProcess:
        if len(cmd) >= 2 and cmd[0] == sys.executable and cmd[1] == "main.py":
            cmd = [cmd[0], "-u", *cmd[1:]]
        return self._run_command_streaming(cmd, cwd=self.mediacrawler_path, timeout=timeout)

    def _run_command_streaming(
        self,
        cmd: List[str],
        *,
        cwd: Path,
        timeout: int,
    ) -> subprocess.CompletedProcess:
        stdout_lines: List[str] = []
        stderr_lines: List[str] = []
        process = subprocess.Popen(
            cmd,
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            errors="replace",
            bufsize=1,
            env={**os.environ, "PYTHONUNBUFFERED": "1"},
        )

        def collect(pipe, source: str, lines: List[str]) -> None:
            if pipe is None:
                return
            for raw_line in iter(pipe.readline, ""):
                line = raw_line.rstrip("\r\n")
                if line:
                    lines.append(line)
                    self._relay_subprocess_line(source, line)
            pipe.close()

        threads = [
            threading.Thread(target=collect, args=(process.stdout, "stdout", stdout_lines), daemon=True),
            threading.Thread(target=collect, args=(process.stderr, "stderr", stderr_lines), daemon=True),
        ]
        for thread in threads:
            thread.start()

        try:
            return_code = process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
            for thread in threads:
                thread.join(timeout=1)
            raise

        for thread in threads:
            thread.join(timeout=1)

        return subprocess.CompletedProcess(
            args=cmd,
            returncode=return_code,
            stdout="\n".join(stdout_lines),
            stderr="\n".join(stderr_lines),
        )

    def _relay_subprocess_output(self, stdout: str | None, stderr: str | None) -> None:
        for line in (stdout or "").splitlines():
            if line:
                self._relay_subprocess_line("stdout", line)
        for line in (stderr or "").splitlines():
            if line:
                self._relay_subprocess_line("stderr", line)

    def _relay_subprocess_line(self, source: str, line: str) -> None:
        if source == "stderr":
            logger.error(line)
        else:
            logger.info(line)
        if self.log_callback:
            self.log_callback(source, line)

    def _count_platform_records(self, platform: str) -> Dict[str, int]:
        tables = PLATFORM_RECORD_TABLES.get(platform)
        if not tables:
            return {"notes": 0, "comments": 0}

        db_dialect = load_runtime_database_config(config.settings).dialect
        try:
            if db_dialect in ("postgresql", "postgres"):
                return self._count_postgres_records(*tables)
            if db_dialect in ("mysql", "mariadb"):
                return self._count_mysql_records(*tables)
            if db_dialect == "sqlite":
                return self._count_sqlite_records(*tables)
        except Exception as exc:
            logger.warning(f"统计 {platform} 入库数量失败: {exc}")
        return {"notes": 0, "comments": 0}

    @staticmethod
    def _count_postgres_records(note_table: str, comment_table: str) -> Dict[str, int]:
        import psycopg

        runtime_db = load_runtime_database_config(config.settings)
        with psycopg.connect(
            host=runtime_db.host,
            port=runtime_db.port,
            dbname=runtime_db.name,
            user=runtime_db.user,
            password=runtime_db.password,
        ) as conn:
            return {
                "notes": PlatformCrawler._count_table_rows(conn, note_table),
                "comments": PlatformCrawler._count_table_rows(conn, comment_table),
            }

    @staticmethod
    def _count_mysql_records(note_table: str, comment_table: str) -> Dict[str, int]:
        import pymysql

        runtime_db = load_runtime_database_config(config.settings)
        with pymysql.connect(
            host=runtime_db.host,
            port=int(runtime_db.port),
            database=runtime_db.name,
            user=runtime_db.user,
            password=runtime_db.password,
        ) as conn:
            return {
                "notes": PlatformCrawler._count_table_rows(conn, note_table),
                "comments": PlatformCrawler._count_table_rows(conn, comment_table),
            }

    @staticmethod
    def _count_sqlite_records(note_table: str, comment_table: str) -> Dict[str, int]:
        import sqlite3

        sqlite_path = (
            os.getenv("BETTAFISH_CRAWLER_SQLITE_PATH")
            or Path.cwd() / "database" / "sqlite_tables.db"
        )
        with sqlite3.connect(sqlite_path) as conn:
            return {
                "notes": PlatformCrawler._count_table_rows(conn, note_table),
                "comments": PlatformCrawler._count_table_rows(conn, comment_table),
            }

    @staticmethod
    def _count_table_rows(conn, table_name: str) -> int:
        cursor = None
        try:
            cursor = conn.cursor()
            cursor.execute(f"SELECT COUNT(*) FROM {table_name}")
            row = cursor.fetchone()
            return int(row[0] if row else 0)
        except Exception:
            return 0
        finally:
            if cursor is not None:
                try:
                    cursor.close()
                except Exception:
                    pass
    
    def run_multi_platform_crawl_by_keywords(
        self,
        keywords: List[str],
        platforms: List[str],
        login_type: str = "qrcode",
        max_notes_per_keyword: int = 50,
        headless: bool = True,
        start_date: str | date | None = None,
        end_date: str | date | None = None,
        crawl_depth: int = 3,
    ) -> Dict:
        """
        基于关键词的多平台爬取 - 每个关键词在所有平台上都进行爬取
        
        Args:
            keywords: 关键词列表
            platforms: 平台列表
            login_type: 登录方式
            max_notes_per_keyword: 每个关键词在每个平台的最大爬取数量
            crawl_depth: 逻辑爬取深度，预留给平台适配器控制详情链路
        
        Returns:
            总体爬取统计
        """
        
        start_message = f"\n🚀 开始全平台关键词爬取"
        start_message += f"\n   关键词数量: {len(keywords)}"
        start_message += f"\n   平台数量: {len(platforms)}"
        start_message += f"\n   登录方式: {login_type}"
        start_message += f"\n   爬取深度: {crawl_depth}"
        start_message += f"\n   每个关键词在每个平台的最大爬取数量: {max_notes_per_keyword}"
        start_message += f"\n   总爬取任务: {len(keywords)} × {len(platforms)} = {len(keywords) * len(platforms)}"
        logger.info(start_message)
        
        total_stats = {
            "total_keywords": len(keywords),
            "total_platforms": len(platforms),
            "total_tasks": len(keywords) * len(platforms),
            "successful_tasks": 0,
            "failed_tasks": 0,
            "total_notes": 0,
            "total_comments": 0,
            "keyword_results": {},
            "platform_summary": {}
        }
        
        # 初始化平台统计
        for platform in platforms:
            total_stats["platform_summary"][platform] = {
                "successful_keywords": 0,
                "failed_keywords": 0,
                "total_notes": 0,
                "total_comments": 0
            }
        
        # 对每个平台一次性爬取所有关键词
        for platform in platforms:
            logger.info(f"\n📝 在 {platform} 平台爬取所有关键词")
            logger.info(f"   关键词: {', '.join(keywords[:5])}{'...' if len(keywords) > 5 else ''}")
            
            try:
                # 一次性传递所有关键词给平台
                result = self.run_crawler(
                    platform,
                    keywords,
                    login_type,
                    max_notes_per_keyword,
                    headless,
                    start_date=start_date,
                    end_date=end_date,
                )
                
                if result.get("success"):
                    total_stats["successful_tasks"] += len(keywords)
                    total_stats["platform_summary"][platform]["successful_keywords"] = len(keywords)
                    
                    notes_count = result.get("notes_count", 0)
                    comments_count = result.get("comments_count", 0)
                    
                    total_stats["total_notes"] += notes_count
                    total_stats["total_comments"] += comments_count
                    total_stats["platform_summary"][platform]["total_notes"] = notes_count
                    total_stats["platform_summary"][platform]["total_comments"] = comments_count
                    total_stats["platform_summary"][platform]["sentiment"] = result.get("sentiment", {})
                    
                    # 为每个关键词记录结果
                    for keyword in keywords:
                        if keyword not in total_stats["keyword_results"]:
                            total_stats["keyword_results"][keyword] = {}
                        total_stats["keyword_results"][keyword][platform] = result
                    
                    logger.info(f"   ✅ 爬取成功")
                else:
                    total_stats["failed_tasks"] += len(keywords)
                    total_stats["platform_summary"][platform]["failed_keywords"] = len(keywords)
                    
                    # 为每个关键词记录失败结果
                    for keyword in keywords:
                        if keyword not in total_stats["keyword_results"]:
                            total_stats["keyword_results"][keyword] = {}
                        total_stats["keyword_results"][keyword][platform] = result
                    
                    logger.error(f"   ❌ 失败: {result.get('error', '未知错误')}")
            
            except Exception as e:
                total_stats["failed_tasks"] += len(keywords)
                total_stats["platform_summary"][platform]["failed_keywords"] = len(keywords)
                error_result = {"success": False, "error": str(e)}
                
                # 为每个关键词记录异常结果
                for keyword in keywords:
                    if keyword not in total_stats["keyword_results"]:
                        total_stats["keyword_results"][keyword] = {}
                    total_stats["keyword_results"][keyword][platform] = error_result
                
                logger.error(f"   ❌ 异常: {e}")
        
        # 打印详细统计
        finish_message = f"\n📊 全平台关键词爬取完成!"
        finish_message += f"\n   总任务: {total_stats['total_tasks']}"
        finish_message += f"\n   成功: {total_stats['successful_tasks']}"
        finish_message += f"\n   失败: {total_stats['failed_tasks']}"
        finish_message += f"\n   成功率: {total_stats['successful_tasks']/total_stats['total_tasks']*100:.1f}%"
        logger.info(finish_message)
        
        platform_summary_message = f"\n📈 各平台统计:"
        for platform, stats in total_stats["platform_summary"].items():
            success_rate = stats["successful_keywords"] / len(keywords) * 100 if keywords else 0
            platform_summary_message += f"\n   {platform}: {stats['successful_keywords']}/{len(keywords)} 关键词成功 ({success_rate:.1f}%)"
        logger.info(platform_summary_message)
        
        return total_stats
    
    def get_crawl_statistics(self) -> Dict:
        """获取爬取统计信息"""
        return {
            "platforms_crawled": list(self.crawl_stats.keys()),
            "total_platforms": len(self.crawl_stats),
            "detailed_stats": self.crawl_stats
        }
    
    def save_crawl_log(self, log_path: str = None):
        """保存爬取日志"""
        if not log_path:
            log_path = f"crawl_log_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        
        try:
            with open(log_path, 'w', encoding='utf-8') as f:
                json.dump(self.crawl_stats, f, ensure_ascii=False, indent=2)
            logger.info(f"爬取日志已保存到: {log_path}")
        except Exception as e:
            logger.exception(f"保存爬取日志失败: {e}")

if __name__ == "__main__":
    # 测试平台爬虫管理器
    crawler = PlatformCrawler()
    
    # 测试配置
    test_keywords = ["科技", "AI", "编程"]
    result = crawler.run_crawler("xhs", test_keywords, max_notes=5)
    
    logger.info(f"测试结果: {result}")
    logger.info("平台爬虫管理器测试完成！")
