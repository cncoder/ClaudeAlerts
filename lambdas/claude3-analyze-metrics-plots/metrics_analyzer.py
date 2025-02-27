import json
import urllib.request
import urllib.error
import logging
import boto3
import time
import xml.etree.ElementTree as ET
from datetime import datetime
from typing import Dict, List, Optional, Tuple
from urllib.parse import urlparse

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger()

class Config:
    """配置类"""
    # API配置
    API_HOST = "10.XX.30.2XX"
    DIFY_ENDPOINT = f"http://{API_HOST}/v1/workflows/run"
    DIFY_API_KEY = "app-ePf7XXXXXN99NdN"
    DIFY_TIMEOUT = 60  # API调用超时时间(秒)
    DIFY_MAX_RETRIES = 3  # 最大重试次数
    DIFY_RETRY_DELAY = 5  # 重试间隔(秒)
    
    # Lark配置
    LARK_WEBHOOK = "https://open.larksuite.com/open-apis/bot/v2/hook/477XXXXXX4ab5"
    LARK_TIMEOUT = 10
    
    # S3配置
    S3_URL_EXPIRY = 3600
    
    # 指标配置
    METRICS_CONFIG = {
        'cpu': {'name': 'CPU', 'unit': '%', 'color': 'blue'},
        'memory': {'name': 'Memory', 'unit': '%', 'color': 'purple'},
        'network': {'name': 'Network', 'unit': 'Mbps', 'color': 'grey'}
    }
    
    # 处理配置
    MIN_REMAINING_TIME = 30

    # 异常级别配置
    SEVERITY_LEVELS = {
        'critical': {
            'keywords': ['严重', '危险', '异常'],
            'icon': '🔴',
            'color': 'red',
            'title_color': 'red'
        },
        'warning': {
            'keywords': ['警告', '波动', '偏离'],
            'icon': '⚠️',
            'color': 'orange',
            'title_color': 'yellow'
        },
        'info': {
            'keywords': ['提示', '建议'],
            'icon': 'ℹ️',
            'color': 'blue',
            'title_color': 'blue'
        }
    }

class DifyAPIError(Exception):
    """Dify API调用异常"""
    pass

class S3Client:
    """S3操作客户端"""
    def __init__(self):
        self.client = boto3.client('s3')
        
    def get_presigned_url(self, bucket: str, key: str) -> str:
        """生成预签名URL"""
        try:
            logger.info(f"生成S3预签名URL - Bucket: {bucket}, Key: {key}")
            url = self.client.generate_presigned_url(
                'get_object',
                Params={'Bucket': bucket, 'Key': key},
                ExpiresIn=Config.S3_URL_EXPIRY
            )
            logger.info(f"生成预签名URL成功")
            return url
        except Exception as e:
            logger.error(f"生成预签名URL失败: {str(e)}")
            raise

    @staticmethod
    def parse_s3_url(s3_url: str) -> Tuple[str, str]:
        """解析S3 URL"""
        try:
            logger.debug(f"解析S3 URL: {s3_url}")
            parsed = urlparse(s3_url)
            bucket = parsed.netloc
            key = parsed.path.lstrip('/')
            return bucket, key
        except Exception as e:
            logger.error(f"解析S3 URL失败: {str(e)}")
            raise

def get_metric_type(source_file: str) -> Optional[str]:
    """从文件名获取指标类型"""
    filename = source_file.lower()
    
    if 'cpu' in filename:
        return 'cpu'
    elif 'memory' in filename:
        return 'memory'
    elif 'network' in filename:
        return 'network'
    return None

def make_http_request(url: str, method: str, headers: Dict, data: Optional[Dict] = None, timeout: int = 30) -> Dict:
    """通用HTTP请求函数"""
    try:
        logger.info(f"发起HTTP请求 - URL: {url}, Method: {method}")
        
        request = urllib.request.Request(
            url,
            data=json.dumps(data).encode('utf-8') if data else None,
            headers=headers,
            method=method
        )
        
        with urllib.request.urlopen(request, timeout=timeout) as response:
            response_data = response.read()
            logger.info(f"请求成功 - 状态码: {response.status}")
            try:
                return json.loads(response_data)
            except json.JSONDecodeError:
                logger.error(f"响应数据解析失败: {response_data}")
                raise
                
    except urllib.error.HTTPError as e:
        logger.error(f"HTTP请求失败: {e.code} {e.reason}")
        logger.error(f"响应内容: {e.read().decode('utf-8')}")
        raise
    except urllib.error.URLError as e:
        logger.error(f"URL错误: {str(e)}")
        raise
    except Exception as e:
        logger.error(f"请求异常: {str(e)}")
        raise

class DifyClient:
    """Dify API客户端"""
    def __init__(self):
        self.endpoint = Config.DIFY_ENDPOINT
        self.api_key = Config.DIFY_API_KEY
        self.s3_client = S3Client()

    def _call_dify_api(self, payload: Dict) -> Dict:
        """调用Dify API并处理重试"""
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }
        
        for attempt in range(Config.DIFY_MAX_RETRIES):
            try:
                logger.info(f"调用Dify API (尝试 {attempt + 1}/{Config.DIFY_MAX_RETRIES})")
                response = make_http_request(
                    url=self.endpoint,
                    method='POST',
                    headers=headers,
                    data=payload,
                    timeout=Config.DIFY_TIMEOUT
                )
                
                # 验证响应数据
                if not isinstance(response, dict):
                    raise DifyAPIError(f"非预期的响应类型: {type(response)}")
                
                # 检查data字段
                if 'data' not in response:
                    raise DifyAPIError(f"响应缺少data字段: {response}")
                    
                data = response['data']
                
                # 检查outputs字段
                if 'outputs' not in data:
                    raise DifyAPIError(f"data缺少outputs字段: {data}")
                    
                outputs = data['outputs']
                
                # 检查result字段
                if 'result' not in outputs:
                    raise DifyAPIError(f"outputs缺少result字段: {outputs}")
                
                # 判断是否无异常
                result_xml = outputs.get('result', '')
                if '无异常' in result_xml:
                    logger.info("检测结果:无异常")
                    return {
                        'result': result_xml,
                        'has_anomaly': False
                    }

                # 有异常时验证x字段
                if 'x' not in outputs:
                    raise DifyAPIError(f"异常分析缺少x字段: {outputs}")
                
                logger.info("检测结果:发现异常")
                return {
                    'result': result_xml,
                    'x': outputs['x'],
                    'has_anomaly': True
                }
                
            except Exception as e:
                logger.error(f"Dify API调用失败: {str(e)}")
                if attempt < Config.DIFY_MAX_RETRIES - 1:
                    logger.info(f"等待 {Config.DIFY_RETRY_DELAY} 秒后重试")
                    time.sleep(Config.DIFY_RETRY_DELAY)
                else:
                    raise DifyAPIError(f"达到最大重试次数: {str(e)}")

    def parse_analysis_xml(self, result_xml: str, analysis_xml: str) -> dict:
        """解析基础结果和分析结果XML"""
        try:
            logger.info("开始解析XML数据")
            # 验证XML数据
            if not result_xml or not analysis_xml:
                raise ValueError("XML数据为空")
            
            # 解析result XML
            result_root = ET.fromstring(result_xml)
            pod_name = result_root.find('pod1').text if result_root.find('pod1') is not None else ''
            
            # 解析analysis XML 
            analysis_root = ET.fromstring(analysis_xml)
            
            # 解析异常pod信息
            results = []
            for pod in analysis_root.findall('.//anomaly_pods/pod'):
                pod_data = {
                    'name': pod.find('name').text if pod.find('name') is not None else '',
                    'confidence': pod.find('confidence').text if pod.find('confidence') is not None else '',
                    'priority': pod.find('priority').text if pod.find('priority') is not None else '',
                    'probable_cause': pod.find('probable_cause').text if pod.find('probable_cause') is not None else '',
                    'action': [],
                    'command': [],
                    'investigation': []
                }
                
                # 获取action列表
                action = pod.find('action')
                if action is not None and action.text:
                    pod_data['action'] = [step.strip() for step in action.text.split('\n') if step.strip()]
                    
                # 获取command列表    
                command = pod.find('command')
                if command is not None and command.text:
                    pod_data['command'] = [cmd.strip() for cmd in command.text.split('\n') if cmd.strip()]
                    
                # 获取investigation列表
                investigation = pod.find('investigation')
                if investigation is not None and investigation.text:
                    pod_data['investigation'] = [step.strip() for step in investigation.text.split('\n') if step.strip()]
                    
                results.append(pod_data)
                
            # 解析summary信息
            summary = analysis_root.find('.//summary')
            summary_data = {
                'total_pods': summary.find('total_pods').text if summary.find('total_pods') is not None else '0',
                'risk_level': summary.find('risk_level').text if summary.find('risk_level') is not None else '',
                'urgent_actions': []
            }
            
            # 获取urgent_actions列表
            urgent_actions = summary.find('urgent_actions')
            if urgent_actions is not None and urgent_actions.text:
                summary_data['urgent_actions'] = [action.strip() for action in urgent_actions.text.split('\n') if action.strip()]
            
            return {
                'pod_name': pod_name,
                'pods': results,
                'summary': summary_data
            }

        except ET.ParseError as e:
            logger.error(f"XML解析错误: {str(e)}")
            raise
        except Exception as e:
            logger.error(f"数据处理错误: {str(e)}")
            raise

    def analyze_plots(self, plots_data: List[Dict], metric_type: str) -> List[Dict]:
        """分析图表数据"""
        if not plots_data:
            logger.warning("plots_data为空")
            return []
            
        results = []
        
        for plot in plots_data:
            try:
                logger.info(f"处理图表 - Service: {plot.get('service')}")
                bucket, key = self.s3_client.parse_s3_url(plot['plot_path'])
                presigned_url = self.s3_client.get_presigned_url(bucket, key)
                
                payload = {
                    "inputs": {
                        metric_type: {
                            "type": "image",
                            "transfer_method": "remote_url",
                            "url": presigned_url
                        }
                    },
                    "response_mode": "blocking",
                    "user": "lambda-user"
                }
                
                # 调用Dify API
                api_result = self._call_dify_api(payload)
                
                if not api_result.get('has_anomaly'):
                    # 无异常情况,跳过
                    logger.info(f"Service {plot['service']} 未发现异常")
                    continue
                
                # 有异常情况
                result_xml = api_result['result']
                analysis_xml = api_result['x']
                
                if result_xml and analysis_xml:
                    analysis = self.parse_analysis_xml(result_xml, analysis_xml)
                    if analysis:
                        results.append({
                            'service': plot['service'],
                            'analysis': analysis,
                            'plot_url': presigned_url
                        })
                    else:
                        logger.warning(f"跳过无效的分析结果 - Service: {plot.get('service')}")
                else:
                    logger.warning(f"Dify返回的XML数据为空 - Service: {plot.get('service')}")

            except Exception as e:
                logger.error(f"处理图表失败 {plot.get('service', 'unknown')}: {str(e)}")

        return results

class LarkBot:
    """Lark机器人客户端"""
    def __init__(self):
        self.webhook = Config.LARK_WEBHOOK

    def create_service_card(self, service_result: dict, metric_type: str) -> dict:
        """创建服务分析卡片"""
        service_name = service_result['service']
        analysis_data = service_result['analysis']
        plot_url = service_result['plot_url']
        
        # 获取summary信息
        summary = analysis_data['summary']
        risk_level = summary['risk_level']
        
        # 确定风险级别颜色
        severity_color = 'blue'
        if risk_level == '高危':
            severity_color = 'red'
        elif risk_level == '中危':
            severity_color = 'orange'
        
        card = {
            "header": {
                "template": severity_color,
                "title": {
                    "content": f"{service_name} 分析结果",
                    "tag": "plain_text"
                }
            },
            "elements": [
                # 添加summary信息
                {
                    "tag": "div",
                    "text": {
                        "content": (
                            f"**风险等级**: {risk_level}\n"
                            f"**异常Pod数量**: {summary['total_pods']}\n"
                            "\n**紧急措施**:\n" +
                            "\n".join([f"• {action}" for action in summary['urgent_actions']])
                        ),
                        "tag": "lark_md"
                    }
                },
                {"tag": "hr"}
            ]
        }

        # 添加每个异常pod的详细信息
        for pod in analysis_data['pods']:
            if pod['name']:  # 只添加有效的Pod信息
                pod_info = {
                    "tag": "div",
                    "text": {
                        "content": (
                            f"🔍 **Pod**: {pod['name']}\n"
                            f"**置信度**: {pod['confidence']} [📊查看监控]({plot_url})\n"
                            f"**优先级**: {pod['priority']}\n"
                            f"**可能原因**: {pod['probable_cause']}"
                        ),
                        "tag": "lark_md"
                    }
                }
                card["elements"].append(pod_info)
                
                # 添加操作建议
                if pod['action']:
                    actions = ["**建议措施**:"]
                    for action in pod['action']:
                        actions.append(f"• {action}")
                    card["elements"].append({
                        "tag": "div",
                        "text": {
                            "content": "\n".join(actions),
                            "tag": "lark_md"
                        }
                    })
                    
                # 添加命令建议    
                if pod['command']:
                    commands = ["**推荐命令**:"]
                    for cmd in pod['command']:
                        commands.append(f"`{cmd}`")
                    card["elements"].append({
                        "tag": "div",
                        "text": {
                            "content": "\n".join(commands),
                            "tag": "lark_md"
                        }
                    })
                    
                # 添加调查建议
                if pod['investigation']:
                    investigations = ["**调查步骤**:"]
                    for step in pod['investigation']:
                        investigations.append(f"• {step}")
                    card["elements"].append({
                        "tag": "div",
                        "text": {
                            "content": "\n".join(investigations),
                            "tag": "lark_md"
                        }
                    })
                    
                card["elements"].append({"tag": "hr"})

        return card

    def send_message(self, results: List[Dict], source_csv: str, time_window: int, metric_type: str) -> None:
        """发送消息到Lark"""
        try:
            # 验证结果列表
            if not results:
                logger.info("没有异常结果,跳过发送消息")
                return
                
            logger.info("准备发送Lark消息")
            current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            metric_config = Config.METRICS_CONFIG[metric_type]
            
            # 创建主卡片
            main_card = {
                "config": {
                    "wide_screen_mode": True
                },
                "header": {
                    "template": metric_config['color'],
                    "title": {
                        "content": f"{metric_config['name']}指标分析报告",
                        "tag": "plain_text"
                    }
                },
                "elements": [
                    {
                        "tag": "div",
                        "text": {
                            "content": (
                                f"**时间**: {current_time}\n"
                                f"**数据源**: {source_csv}\n"
                                f"**分析时间窗口**: {time_window}小时"
                            ),
                            "tag": "lark_md"
                        }
                    },
                    {"tag": "hr"}
                ]
            }

            # 添加每个服务的分析卡片
            for result in results:
                service_card = self.create_service_card(result, metric_type)
                if service_card:
                    main_card["elements"].extend(service_card["elements"])

            message = {
                "msg_type": "interactive",
                "card": main_card
            }
            
            logger.info("发送消息到Lark")
            response = make_http_request(
                url=self.webhook,
                method='POST',
                headers={'Content-Type': 'application/json'},
                data=message,
                timeout=Config.LARK_TIMEOUT
            )
            
            logger.info("Lark消息发送成功")

        except Exception as e:
            logger.error(f"发送Lark消息失败: {str(e)}")
            raise

def lambda_handler(event, context):
    """Lambda处理函数"""
    try:
        logger.info("Lambda函数开始执行")
        
        if 'Records' not in event or not event['Records']:
            logger.error("事件中没有Records字段或Records为空")
            return {'statusCode': 400, 'body': 'Invalid event structure'}
            
        for record in event['Records']:
            request_id = record['messageId']
            logger.info(f"开始处理消息 - RequestID: {request_id}")
            
            try:
                # 检查剩余执行时间
                remaining_time = context.get_remaining_time_in_millis() / 1000
                if remaining_time < Config.MIN_REMAINING_TIME:
                    logger.warning(
                        f"剩余执行时间不足: {remaining_time}秒，"
                        f"需要至少{Config.MIN_REMAINING_TIME}秒"
                    )
                    continue
                
                message = json.loads(record['body'])
                
                plots = message['plots']
                time_window = message['time_window_hours']
                source_csv = message['source_csv']
                
                metric_type = message.get('metric_type') or get_metric_type(source_csv)
                if not metric_type:
                    raise ValueError(f"无法确定指标类型: {source_csv}")

                logger.info(f"处理指标类型: {metric_type}")
                
                # 调用Dify分析
                dify_client = DifyClient()
                analysis_results = dify_client.analyze_plots(plots, metric_type)
                
                if analysis_results:
                    # 发送Lark消息
                    logger.info("发送分析结果到Lark")
                    lark_bot = LarkBot()
                    lark_bot.send_message(
                        analysis_results,
                        source_csv,
                        time_window,
                        metric_type
                    )
                else:
                    logger.info("未发现异常,跳过发送消息")
                
                logger.info("消息处理完成")

            except Exception as e:
                logger.error(f"处理消息时发生错误: {str(e)}")
                continue

        return {
            'statusCode': 200,
            'body': 'Successfully processed all records'
        }

    except Exception as e:
        logger.error(f"Lambda执行失败: {str(e)}")
        return {
            'statusCode': 500,
            'body': f'Lambda execution failed: {str(e)}'
        }