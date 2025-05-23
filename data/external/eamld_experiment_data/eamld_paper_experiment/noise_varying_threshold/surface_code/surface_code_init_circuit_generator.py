import stim
import os
from epmld import CircuitGenerator


def generate_experiment_noisy_circuit(code_task, d, r, p, noise_model = "si1000", gate = "cz"):
    # 生成 surface_code 电路
    circuit_gen = CircuitGenerator()
    circuit_str = circuit_gen.generate_circuit(code_task, d, r, noise_model, p, gate)
    
    code_circuit = stim.Circuit(circuit_str)

    # 将电路转换为字符串
    circuit_string = str(code_circuit)

    # 设置文件保存路径
    current_file_directory = os.path.dirname(os.path.abspath(__file__))
    file_name = f"circuit_noisy_{noise_model}_p{int(p*10000)}.stim"

    # 根据 code_task 决定文件夹
    if "memory_z" in code_task:
        folder = "Z"
    elif "memory_x" in code_task:
        folder = "X"
    else:
        folder = "other"

    # 构建文件路径
    file_path = os.path.join(current_file_directory, folder, f"d{d}_r{r}", file_name)
    
    # Ensure the output directory exists
    output_dir = os.path.dirname(file_path)
    if not os.path.exists(output_dir):
        os.makedirs(output_dir)  # Create the directory if it doesn't exist

    # 检查文件夹是否存在
    if not os.path.exists(os.path.dirname(file_path)):
        raise FileNotFoundError(f"The directory {os.path.dirname(file_path)} does not exist.")
    
    # 保存电路字符串到文件
    with open(file_path, 'w') as file:
        file.write(circuit_string)

    print(f"Circuit saved to: {file_path}")

if __name__ == "__main__":
    code_tasks = ["surface_code:rotated_memory_x","surface_code:rotated_memory_z"]
    # distances = [3, 5, 7, 9]
    # probabilities = [1, 5, 10, 20, 30, 40, 50, 60, 70, 80, 90, 100]
    # probabilities = [0.01, 0.1, 0.5, 1, 5, 10, 20, 30, 40, 50, 60, 70, 80, 90, 100]
    # probabilities = [0.01, 0.1, 0.5 , 1, 10, 20, 40, 60, 80, 100, 200, 500, 1000] #更新更大范围，研究噪声对于方法的影响

    distances = [3, 5, 7]
    probabilities = [1,  10,  20,  30,  40,  50,  60,  70,  80,  90, 100, 110, 120, 130, 140, 150]

    for code_task in code_tasks:
        for d in distances:
            for p in probabilities:
                for r in [1, d]:
                    # 生成初始的surface code 含噪声线路
                    generate_experiment_noisy_circuit(code_task = code_task, d=d, r=r, p=float(p/10000))