"""
报告结构生成节点
负责根据查询生成报告的整体结构
"""

from json.decoder import JSONDecodeError
import json
from typing import Any, Dict, List

from loguru import logger

from .base_node import StateMutationNode
from ..modes import InsightModePreset, get_insight_mode_preset
from ..state.state import State
from ..prompts import build_report_structure_prompt
from ..utils.text_processing import (
    clean_json_tags,
    extract_clean_response,
    fix_incomplete_json,
    remove_reasoning_from_output,
)


class ReportStructureNode(StateMutationNode):
    """生成报告结构的节点"""
    
    def __init__(
        self,
        llm_client,
        query: str,
        mode_preset: InsightModePreset | None = None,
    ):
        """
        初始化报告结构节点
        
        Args:
            llm_client: LLM客户端
            query: 用户查询
        """
        super().__init__(llm_client, "ReportStructureNode")
        self.query = query
        self.mode_preset = mode_preset or get_insight_mode_preset()
        self.system_prompt = build_report_structure_prompt(self.mode_preset)
    
    def validate_input(self, input_data: Any) -> bool:
        """验证输入数据"""
        return isinstance(self.query, str) and len(self.query.strip()) > 0
    
    def run(self, input_data: Any = None, **kwargs) -> List[Dict[str, str]]:
        """
        调用LLM生成报告结构
        
        Args:
            input_data: 输入数据（这里不使用，使用初始化时的query）
            **kwargs: 额外参数
            
        Returns:
            报告结构列表
        """
        try:
            logger.info(f"正在为查询生成报告结构: {self.query}")
            
            # 调用LLM（流式，安全拼接UTF-8）
            response = self.llm_client.stream_invoke_to_string(
                self.system_prompt,
                self.query,
            )
            
            # 处理响应
            processed_response = self.process_output(response)
            
            logger.info(f"成功生成 {len(processed_response)} 个段落结构")
            return processed_response
            
        except Exception as e:
            logger.exception(f"生成报告结构失败: {str(e)}")
            raise e
    
    def process_output(self, output: str) -> List[Dict[str, str]]:
        """
        处理LLM输出，提取报告结构
        
        Args:
            output: LLM原始输出
            
        Returns:
            处理后的报告结构列表
        """
        try:
            # 清理响应文本
            cleaned_output = remove_reasoning_from_output(output)
            cleaned_output = clean_json_tags(cleaned_output)
            
            # 记录清理后的输出用于调试
            logger.info(f"清理后的输出: {cleaned_output}")
            
            # 解析JSON
            try:
                report_structure = json.loads(cleaned_output)
                logger.info("JSON解析成功")
            except JSONDecodeError as e:
                logger.error(f"JSON解析失败: {str(e)}")
                # 使用更强大的提取方法
                report_structure = extract_clean_response(cleaned_output)
                if "error" in report_structure:
                    logger.error("JSON解析失败，尝试修复...")
                    # 尝试修复JSON
                    fixed_json = fix_incomplete_json(cleaned_output)
                    if fixed_json:
                        try:
                            report_structure = json.loads(fixed_json)
                            logger.info("JSON修复成功")
                        except JSONDecodeError:
                            logger.error("JSON修复失败")
                            # 返回默认结构
                            return self._generate_default_structure()
                    else:
                        logger.error("无法修复JSON，使用默认结构")
                        return self._generate_default_structure()
            
            # 验证结构
            if not isinstance(report_structure, list):
                logger.info("报告结构不是列表，尝试转换...")
                if isinstance(report_structure, dict):
                    # 如果是单个对象，包装成列表
                    report_structure = [report_structure]
                else:
                    logger.exception("报告结构格式无效，使用默认结构")
                    return self._generate_default_structure()
            
            # 验证每个段落
            validated_structure = []
            for i, paragraph in enumerate(report_structure):
                if not isinstance(paragraph, dict):
                    logger.warning(f"段落 {i+1} 不是字典格式，跳过")
                    continue
                
                title = paragraph.get("title", f"段落 {i+1}")
                content = paragraph.get("content", "")
                
                if not title or not content:
                    logger.warning(f"段落 {i+1} 缺少标题或内容，跳过")
                    continue
                
                validated_structure.append({
                    "title": title,
                    "content": content
                })
            
            if not validated_structure:
                logger.warning("没有有效的段落结构，使用默认结构")
                return self._generate_default_structure()
            
            normalized_structure = self._fit_structure_to_mode(validated_structure)
            logger.info(f"成功验证 {len(normalized_structure)} 个段落结构")
            return normalized_structure
            
        except Exception as e:
            logger.exception(f"处理输出失败: {str(e)}")
            return self._generate_default_structure()
    
    def _generate_default_structure(self) -> List[Dict[str, str]]:
        """
        生成默认的报告结构
        
        Returns:
            默认的报告结构列表
        """
        logger.info("生成默认报告结构")
        defaults = [
            {"title": section.title, "content": section.content}
            for section in self.mode_preset.fallback_sections
        ]
        target_count = self.mode_preset.paragraph_count
        while len(defaults) < target_count:
            next_index = len(defaults) + 1
            defaults.append(
                {
                    "title": f"补充分析 {next_index}",
                    "content": f"围绕查询主题进行第 {next_index} 个维度的补充分析。",
                }
            )
        return defaults[:target_count]

    def _fit_structure_to_mode(
        self,
        report_structure: List[Dict[str, str]],
    ) -> List[Dict[str, str]]:
        """Trim or backfill paragraphs so mode paragraph count is stable."""

        target_count = self.mode_preset.paragraph_count
        if len(report_structure) > target_count:
            logger.warning(
                f"LLM生成了 {len(report_structure)} 个段落，"
                f"当前模式仅保留前 {target_count} 个"
            )
        normalized = report_structure[:target_count]
        if len(normalized) < target_count:
            logger.warning(
                f"LLM生成了 {len(normalized)} 个有效段落，"
                f"当前模式需要 {target_count} 个，将使用默认段落补齐"
            )
            defaults = self._generate_default_structure()
            normalized.extend(defaults[len(normalized):target_count])
        return normalized
    
    def mutate_state(self, input_data: Any = None, state: State = None, **kwargs) -> State:
        """
        将报告结构写入状态
        
        Args:
            input_data: 输入数据
            state: 当前状态，如果为None则创建新状态
            **kwargs: 额外参数
            
        Returns:
            更新后的状态
        """
        if state is None:
            state = State()
        
        try:
            # 生成报告结构
            report_structure = self.run(input_data, **kwargs)
            
            # 设置查询和报告标题
            state.query = self.query
            if not state.report_title:
                state.report_title = f"关于'{self.query}'的深度研究报告"
            
            # 添加段落到状态
            for paragraph_data in report_structure:
                state.add_paragraph(
                    title=paragraph_data["title"],
                    content=paragraph_data["content"]
                )
            
            logger.info(f"已将 {len(report_structure)} 个段落添加到状态中")
            return state
            
        except Exception as e:
            logger.exception(f"状态更新失败: {str(e)}")
            raise e
