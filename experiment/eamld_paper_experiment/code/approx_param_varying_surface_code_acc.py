import os
import logging
from datetime import datetime
import csv
import eamld
# import pymatching
# from stimbposd import BPOSD
from eamld.benchmark import generate_detector_error_model, LogicalErrorRateBenchmark
# 设置 logging 配置，放在模块级别
from eamld.logging_config import setup_logger

logger = setup_logger("eamld_paper_experiment/approx_param_varying_surface_code_acc", log_level=logging.INFO)

def main():
    # 设置输入数据的相关路径
    related_path = "/home/normaluser/ck/eamld/data/external/eamld_experiment_data/eamld_paper_experiment/approx_param_varying/surface_code"

    # 设置输出目录
    output_dir="/home/normaluser/ck/eamld/experiment/eamld_paper_experiment/result"
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_file = os.path.join(output_dir, f"approx_param_varying_surface_code_acc_results_{timestamp}.csv")

    # 设置Surface code的相关初始化参数
    code_tasks = ["surface_code:rotated_memory_x","surface_code:rotated_memory_z"]
    # distances = [3, 5, 7, 9]
    distances = [3, 9]
    # 固定噪声参数和噪声模型：
    probabilities = [10]
    noise_models = ["si1000"]
    # 只考虑没有数据比特信息的场景
    have_stabilizers = [False]

    # 设置待测试的解码方法：
    decoder_methods = ["EAMLD"]  # r=d的情况下，无损的EAMLD，也是一个非常大的指数，只有在d=3的时候才能跑，因为这里我们只考虑EAMLD，理论上是能够跑完的，10**5。

    # 近似的MLD方法的初始化参数：
    approximatestrategy = "hyperedge_topk"
    approximate_params = [10, 100, 500, 1000]
    # 超图截取的优先级，超图截取的最大数量
    # prioritys = [0, -1, -2]
    # priority_topks = [100, 150, 200]

    prioritys = [0, -1, -2, -3]
    priority_topks = [10, 100, 150, 200]

    # 创建存储结果的CSV文件并写入表头
    fieldnames = ['code_task', 'd', 'round', 'approximate_param', 'priority', 'priority_topk', 'decoder_method', 'logical_error_rate', 'have_stabilizer']
    # 如果文件不存在，写入表头
    if not os.path.exists(output_file):
        with open(output_file, mode='w', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()

    # 使用 with 来避免每次打开文件
    with open(output_file, mode='w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        
        # 添加打印信息
        logger.info("Starting the benchmark process...")

        for code_task in code_tasks:
            error_type = "Z" if "memory_z" in code_task else "X" if "memory_x" in code_task else "other"
            for have_stabilizer in have_stabilizers:
                for d in distances:
                    for p in probabilities:
                        # TODO: 根据所需轮次，修改该部分的代码。
                        # rounds = [1]
                        # rounds = [1, d]
                        rounds = [d]
                        for r in rounds:
                            for noise_model in noise_models:
                                for decoder_method in decoder_methods:
                                    for priority_index in range(len(priority_topks)):
                                        priority = prioritys[priority_index]
                                        priority_topk = priority_topks[priority_index]
                                    # for priority in prioritys:
                                    #     for priority_topk in priority_topks:
                                        for approximate_param in approximate_params:
                                            logger.info(f"Running for {code_task}, d={d}, r={r},probability={p}, noise_model={noise_model}, decoder_method={decoder_method}")
                                            logger.info(f"approximate_param={approximate_param}, priority={priority}, priority_topk={priority_topk}")
                                            # 获取对应的检测器错误率模型，用于构建解码器

                                            dem = generate_detector_error_model(d = d, r=r, p=p, noise_model=noise_model,
                                                                                error_type=error_type, decomposed_error = False,
                                                                                related_path=related_path, have_stabilizer = have_stabilizer)
                                            
                                            # 构建对应的解码器
                                            # 使用EAMLD方法。
                                            decoder =  eamld.EAMLD(detector_error_model=dem,
                                                                        order_method='greedy',
                                                                        slice_method='no_slice',
                                                                        use_approx = True,
                                                                        approximatestrategy = approximatestrategy,
                                                                        approximate_param = approximate_param,
                                                                        contraction_code = "eamld",
                                                                        accuracy = "float64",
                                                                        priority = priority,
                                                                        priority_topk = priority_topk)
                                            
                                            # 创建基准测试对象
                                            benchmark = LogicalErrorRateBenchmark(
                                                decoder_function=decoder,
                                                d=d,
                                                nkd=None,
                                                r=r,
                                                p=p,
                                                noise_model=noise_model,
                                                error_type=error_type,
                                                num_runs=1,
                                                data_path=related_path,
                                                code_name="surface code",
                                                have_stabilizer = have_stabilizer
                                            )

                                            # 运行基准测试并获取结果
                                            logical_error_rate = benchmark.run()[0]
                                            logger.info(f"Logical Error Rate for {code_task}, d={d}, p={p}, noise_model={noise_model}, decoder={decoder_method}: {logical_error_rate}")

                                            # 将结果写入CSV文件
                                            writer.writerow({
                                                'code_task': code_task,
                                                'd': d,
                                                'round': r,
                                                'approximate_param': approximate_param,
                                                'priority': priority,
                                                'priority_topk': priority_topk,
                                                'decoder_method': decoder_method,
                                                'logical_error_rate': logical_error_rate,
                                                'have_stabilizer': have_stabilizer
                                            })

    logger.info(f"Finish the benchmark process.")

if __name__ == "__main__":
    main()

# 后台运行命令
# nohup python /home/normaluser/ck/eamld/experiment/eamld_paper_experiment/code/approx_param_varying_surface_code_acc.py > /home/normaluser/ck/eamld/experiment/eamld_paper_experiment/log/approx_param_varying_surface_code_acc_output.log 2>&1 &
# 3281113