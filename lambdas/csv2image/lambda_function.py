import os
import json
import logging
import boto3
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from datetime import datetime, timedelta
import tempfile

logger = logging.getLogger()
logger.setLevel(logging.INFO)

class Config:
    """配置类"""
    BUCKET_NAME = os.environ['BUCKET_NAME']
    INPUT_PREFIX = os.environ.get('INPUT_PREFIX', 'data/')
    OUTPUT_PREFIX = os.environ.get('OUTPUT_PREFIX', 'plots/')
    QUEUE_URL = os.environ['QUEUE_URL']
    TIME_WINDOW_HOURS = int(os.environ.get('TIME_WINDOW_HOURS', '8'))
    TIME_INTERVAL_MINUTES = 15
    
    # 图表样式配置
    PLOT_DPI = 300
    PLOT_FIGSIZE = (15, 8)
    PLOT_COLORS = ['#1f77b4', '#ff7f0e', '#2ca02c', '#d62728', 
                   '#9467bd', '#8c564b', '#e377c2', '#7f7f7f']
    PLOT_MARKER_SIZE = 4
    PLOT_LINE_WIDTH = 1.5
    PLOT_GRID_ALPHA = 0.2
    
    # 字体配置
    TITLE_FONTSIZE = 12
    LABEL_FONTSIZE = 10
    TICK_FONTSIZE = 9
    LEGEND_FONTSIZE = 9
    
    # 指标配置
    METRICS_CONFIG = {
        'cpu': {
            'value_column': 'cpuusage',
            'ylabel': 'CPU Usage (%)',
            'title_prefix': 'CPU Usage'
        },
        'memory': {
            'value_column': 'memusage',
            'ylabel': 'Memory Usage (%)',
            'title_prefix': 'Memory Usage'
        },
        'network': {
            'value_column': 'netusage',
            'ylabel': 'Network Usage (Mbps)',
            'title_prefix': 'Network Usage'
        }
    }

def get_metric_type(filename: str) -> str:
    """从文件名获取指标类型"""
    filename = filename.lower()
    if 'cpu' in filename:
        return 'cpu'
    elif 'memory' in filename:
        return 'memory'
    elif 'network' in filename:
        return 'network'
    else:
        raise ValueError(f"Cannot determine metric type from filename: {filename}")

def generate_plot(data: pd.DataFrame, service_name: str, config: Config, metric_type: str) -> str:
    """生成指标图表"""
    try:
        max_timestamp = data['timestamp'].max()
        min_timestamp = max_timestamp - timedelta(hours=config.TIME_WINDOW_HOURS)
        
        mask = (data['timestamp'] >= min_timestamp) & (data['timestamp'] <= max_timestamp)
        plot_data = data[mask].iloc[::2]
        
        plt.figure(figsize=config.PLOT_FIGSIZE)
        metric_config = config.METRICS_CONFIG[metric_type]
        value_column = metric_config['value_column']
        
        for idx, (_, group) in enumerate(plot_data.groupby(['pod', 'node'])):
            label = f"{group['pod'].iloc[0]}-{group['node'].iloc[0]}"
            plt.plot(
                group['timestamp'],
                group[value_column],
                label=label,
                color=config.PLOT_COLORS[idx % len(config.PLOT_COLORS)],
                linestyle='-',
                marker='o',
                markersize=config.PLOT_MARKER_SIZE,
                linewidth=config.PLOT_LINE_WIDTH
            )

        time_interval = timedelta(minutes=config.TIME_INTERVAL_MINUTES)
        ticks = pd.date_range(start=min_timestamp, end=max_timestamp, freq=time_interval)
        plt.xticks(ticks, [t.strftime('%Y-%m-%d %H:%M') for t in ticks], rotation=45)
        
        plt.title(f"{metric_config['title_prefix']} - {service_name}\n"
                 f"8-hour window with downsampled data: "
                 f"{min_timestamp.strftime('%Y-%m-%d %H:%M')} to {max_timestamp.strftime('%Y-%m-%d %H:%M')}",
                 fontsize=config.TITLE_FONTSIZE,
                 fontweight='bold')
        
        plt.xlabel('Time', fontsize=config.LABEL_FONTSIZE)
        plt.ylabel(metric_config['ylabel'], fontsize=config.LABEL_FONTSIZE)
        plt.grid(True, linestyle='-', alpha=config.PLOT_GRID_ALPHA)
        plt.xticks(fontsize=config.TICK_FONTSIZE)
        plt.yticks(fontsize=config.TICK_FONTSIZE)
        plt.legend(bbox_to_anchor=(1.02, 1), loc='upper left', 
                  fontsize=config.LEGEND_FONTSIZE, frameon=True, borderaxespad=0.)
        plt.tight_layout()

        temp_file = tempfile.NamedTemporaryFile(
            suffix='.jpg',
            prefix=f'plot_{service_name}_',
            dir='/tmp',
            delete=False
        )
        
        plt.savefig(temp_file.name, dpi=config.PLOT_DPI,
                   bbox_inches='tight', facecolor='white', edgecolor='none')
        plt.close()
        
        return temp_file.name
        
    except Exception as e:
        logger.error(f"Error generating plot: {str(e)}")
        raise

def lambda_handler(event, context):
    """Lambda处理函数 - CSV转图片"""
    try:
        config = Config()
        s3 = boto3.client('s3')
        sqs = boto3.client('sqs')
        
        s3_event = event['Records'][0]['s3']
        bucket = s3_event['bucket']['name']
        key = s3_event['object']['key']
        
        metric_type = get_metric_type(key.split('/')[-1])
        logger.info(f"Processing {metric_type} metrics from file: {bucket}/{key}")
        
        csv_temp = tempfile.NamedTemporaryFile(suffix='.csv', prefix='input_', 
                                             dir='/tmp', delete=False)
        s3.download_file(bucket, key, csv_temp.name)
        
        df = pd.read_csv(csv_temp.name)
        df['timestamp'] = pd.to_datetime(df['timestamp'])
        os.unlink(csv_temp.name)
        
        results = []
        for service_name, service_data in df.groupby('service'):
            plot_file = generate_plot(service_data, service_name, config, metric_type)
            output_key = (f"{config.OUTPUT_PREFIX}{metric_type}/{service_name}/"
                         f"plot_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jpg")
            
            s3.upload_file(plot_file, config.BUCKET_NAME, output_key)
            results.append({
                'service': service_name,
                'plot_path': f"s3://{config.BUCKET_NAME}/{output_key}",
                'source_csv': key,
                'metric_type': metric_type,
                'time_window_hours': config.TIME_WINDOW_HOURS
            })
            os.unlink(plot_file)
        
        sqs.send_message(
            QueueUrl=config.QUEUE_URL,
            MessageBody=json.dumps({
                'timestamp': datetime.now().isoformat(),
                'source_csv': key,
                'metric_type': metric_type,
                'time_window_hours': config.TIME_WINDOW_HOURS,
                'plots': results
            })
        )
        
        return {
            'statusCode': 200,
            'body': json.dumps({
                'message': 'Successfully generated plots',
                'metric_type': metric_type,
                'time_window_hours': config.TIME_WINDOW_HOURS,
                'results': results
            })
        }
        
    except Exception as e:
        logger.error(f"Error processing CSV file: {str(e)}")
        raise