package com.example.lombok;

import lombok.Data;
import org.springframework.stereotype.Service;

@Service
@Data
public class ServiceDataClass {
    private String config;

    public String getConfig() {
        return config;
    }

    public void setConfig(String config) {
        this.config = config;
    }

    public String executeBusinessLogic() {
        return "result";
    }
}
